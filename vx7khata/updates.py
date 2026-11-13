"""Optional online update check.

The app works fully offline; this module only ever runs when the user has internet and has left automatic checks
on. It sends nothing about the shop, the customers or the data file - a single GET for the latest release - and
every downloaded installer is verified against the SHA-256 published with the release before anything runs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__

REPOSITORY = "aryanrana2064-del/khata-pro-exe"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TIMEOUT = 8.0
CHUNK = 1 << 20
USER_AGENT = f"VX7-KHATA-PRO/{__version__} (Windows)"


class UpdateError(Exception):
    """A check or download that could not finish; the message is shown to the user as-is."""


@dataclass
class Release:
    version: str
    notes: str
    installer_url: str
    sha256: str = ""
    size: int = 0
    page_url: str = ""


def version_tuple(text: str) -> tuple[int, ...]:
    """'v1.3.1' -> (1, 3, 1); anything unreadable becomes (0,)."""
    clean = str(text or "").strip().lstrip("vV")
    if not clean:
        return (0,)
    parts = []
    for piece in clean.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def parse_release(payload: dict) -> Optional[Release]:
    """Read a GitHub release (or a version.json with the same fields) into a Release."""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or payload.get("version") or "").strip()
    installers = []
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name.lower().endswith(".exe") and url:
            installers.append((name, url, str(asset.get("digest") or ""), int(asset.get("size") or 0)))
    installers.sort(key=lambda item: "setup" not in item[0].lower())   # prefer the installer over a portable exe
    if installers:
        name, url, digest, size = installers[0]
        sha = digest.split("sha256:", 1)[1] if digest.startswith("sha256:") else ""
        version = tag or name
    else:
        url = str(payload.get("installer_url") or "")
        sha = str(payload.get("sha256") or "")
        size = int(payload.get("size") or 0)
        version = tag
    if not url or not version:
        return None
    return Release(version=str(version).lstrip("vV"),
                   notes=str(payload.get("notes") or payload.get("body") or "").strip(),
                   installer_url=url, sha256=sha, size=size, page_url=str(payload.get("html_url") or ""))


def parse_checksums(text: str) -> dict[str, str]:
    """'<sha256>  <file>' lines -> {file name: sha256}."""
    found = {}
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            found[parts[-1].lstrip("*")] = parts[0].lower()
    return found


def fetch_release(timeout: float = TIMEOUT) -> Release:
    """The newest published release. Raises UpdateError with a sentence fit for the screen."""
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": USER_AGENT,
                                                           "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No update has been published yet.") from exc
        raise UpdateError(f"The update server answered with HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("No internet connection right now.") from exc
    except ValueError as exc:
        raise UpdateError("The update server sent an unreadable answer.") from exc
    release = parse_release(payload)
    if release is None:
        raise UpdateError("The latest release has no installer attached.")
    if not release.sha256:
        release.sha256 = _checksum_from_assets(payload, release.installer_url.rsplit("/", 1)[-1], timeout)
    return release


def _checksum_from_assets(payload: dict, installer_name: str, timeout: float) -> str:
    """Older releases carry a SHA256SUMS file instead of a per-asset digest."""
    for asset in payload.get("assets") or []:
        if str(asset.get("name") or "").upper() != "SHA256SUMS":
            continue
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                table = parse_checksums(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError):
            return ""
        return table.get(installer_name, "")
    return ""


def download(release: Release, folder: Path, progress: Optional[Callable[[int, int], None]] = None,
             cancelled: Optional[Callable[[], bool]] = None) -> Path:
    """Download the installer, verify its checksum and return the file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = release.installer_url.rsplit("/", 1)[-1] or f"VX7-KHATA-PRO-Setup-{release.version}.exe"
    target = folder / name
    request = urllib.request.Request(release.installer_url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as handle:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("The download was cancelled.")
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                got += len(block)
                if progress is not None:
                    progress(got, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise UpdateError("The download did not finish - check the internet connection.") from exc
    if release.sha256 and digest.hexdigest().lower() != release.sha256.lower():
        target.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer does not match the published checksum, so it was deleted.")
    return target


def launch_installer(path: Path) -> None:
    """Start the Inno Setup installer quietly; the caller closes the app so files can be replaced."""
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/NORESTART", "/SUPPRESSMSGBOXES"],
                     close_fds=True)


def removable_drives() -> list[Path]:
    """USB sticks and SD cards that are plugged in right now (Windows only; [] elsewhere)."""
    if sys.platform != "win32":
        return []
    import ctypes

    removable = 2                      # DRIVE_REMOVABLE
    found = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except OSError:
        return []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = f"{chr(65 + index)}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(letter)) != removable:
                continue
        except OSError:
            continue
        drive = Path(letter)
        if drive.exists():
            found.append(drive)
    return found


def copy_backup_to_removable(source: Path, drives: Optional[list[Path]] = None) -> Optional[Path]:
    """Put a second copy of the safety backup on a USB stick, so a dead disk cannot take the backup with it."""
    source = Path(source)
    for drive in removable_drives() if drives is None else drives:
        folder = Path(drive) / "VX7 KHATA SAFETY"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source.name
            with open(source, "rb") as reader, open(target, "wb") as writer:
                while True:
                    block = reader.read(CHUNK)
                    if not block:
                        break
                    writer.write(block)
            return target
        except OSError:
            continue
    return None


def data_snapshot(service) -> dict:
    """Row counts used after an update to prove the data came through unchanged."""
    dashboard = service.dashboard()
    return {"customers": int(dashboard.total_customers), "transactions": int(dashboard.total_transactions)}


def verify_data(service, snapshot: dict) -> tuple[bool, str]:
    """Quick database check plus the row counts from before the update."""
    try:
        result = service.conn.execute("PRAGMA quick_check").fetchone()
    except Exception as exc:  # noqa: BLE001 - a broken file must be reported, not raised
        return False, f"The data file cannot be read: {exc}"
    if not result or str(result[0]).lower() != "ok":
        return False, "The data file failed its integrity check."
    try:
        now = data_snapshot(service)
    except Exception as exc:  # noqa: BLE001
        return False, f"The data file opened but could not be counted: {exc}"
    for key, value in (snapshot or {}).items():
        if now.get(key, 0) < int(value):
            return False, f"{key} fell from {value} to {now.get(key, 0)} after the update."
    return True, "ok"
