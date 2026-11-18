"""Phone Remote: runs the bundled VX7 Remote server (Node.js) so a phone can control this PC.

The server, its Telegram bot and the native Windows helper all live in ``vx7khata/remote`` — the same
backend the standalone VX7 Remote app ships, so the phone link, pairing and PWA stay compatible.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 8765
INFO_URL = f"http://127.0.0.1:{PORT}/api/desktop/info"
CREATE_NO_WINDOW = 0x08000000


def remote_dir() -> Path:
    """The bundled backend; works from source and from the PyInstaller one-folder build."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent / "_internal"))
        return base / "vx7khata" / "remote"
    return Path(__file__).resolve().parent / "remote"


def env_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "vx7-remote" / ".env"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = env_path()
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        except OSError:
            pass
    return values


def save_keys(gemini: str, deepseek: str) -> Path:
    values = read_env()
    values["GEMINI_API_KEY"] = gemini.strip()
    if deepseek.strip():
        values["DEEPSEEK_API_KEY"] = deepseek.strip()
    values.setdefault("GEMINI_MODEL", "gemini-3.1-flash-lite")
    values.setdefault("PORT", str(PORT))
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items() if v) + "\n", encoding="utf-8")
    return path


def find_node() -> str | None:
    bundled = remote_dir() / "runtime" / "node.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("node")
    if found:
        return found
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        root = os.environ.get(env)
        if not root:
            continue
        for sub in (("nodejs",), ("Programs", "nodejs")):
            candidate = Path(root).joinpath(*sub) / "node.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def probe(timeout: float = 0.6) -> dict | None:
    """Info of a running remote server (ours or the standalone app), or None."""
    try:
        with urllib.request.urlopen(INFO_URL, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - not running / not reachable both mean "no info"
        return None


class RemoteController:
    """Owns the child Node process. An already-running server (the standalone app) is adopted, never doubled."""

    def __init__(self, service):
        self.svc = service
        self.proc: subprocess.Popen | None = None
        self.error = ""
        self.external = False

    def start(self, wait: float = 12.0, bridge_token: str = "") -> dict | None:
        info = probe()
        if info:
            self.external = self.proc is None
            return info
        node = find_node()
        if node is None:
            self.error = "Node.js nahi mila — https://nodejs.org se install karo, phir dobara try karo."
            return None
        script = remote_dir() / "src" / "standalone.js"
        if not script.is_file():
            self.error = f"Remote backend files missing: {script}"
            return None
        env = os.environ.copy()
        env["PORT"] = str(PORT)
        from .bridge import BRIDGE_PORT
        env["KHATA_BRIDGE_URL"] = f"http://127.0.0.1:{BRIDGE_PORT}"
        env["KHATA_BRIDGE_TOKEN"] = bridge_token or ""
        appdata = Path(os.environ.get("APPDATA") or Path.home()) / "vx7-remote"
        env_file = appdata / ".env"
        if env_file.is_file():
            env["VX7_ENV_FILE"] = str(env_file)
        appdata.mkdir(parents=True, exist_ok=True)
        log_path = appdata / "remote-server.log"
        creation = CREATE_NO_WINDOW if os.name == "nt" else 0
        with open(log_path, "ab") as log_file:
            self.proc = subprocess.Popen(
                [node, str(script)],
                cwd=str(remote_dir()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=log_file,
                creationflags=creation,
            )
        self.error = ""
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.error = f"Remote server exit ho gaya — log dekho: {log_path}"
                self.proc = None
                return None
            info = probe()
            if info:
                return info
            time.sleep(0.4)
        self.error = "Server start timeout — log dekho: " + str(log_path)
        return None

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()

    def shutdown(self) -> None:
        self.stop()

    def info(self) -> dict | None:
        return probe()

    def owns_server(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def autostart(self) -> bool:
        return self.svc.get_setting("remote_autostart", "1") == "1"

    def set_autostart(self, value: bool) -> None:
        self.svc.set_setting("remote_autostart", "1" if value else "0")
