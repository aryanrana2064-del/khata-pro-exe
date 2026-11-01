"""Talking to the speech engine without ever blocking the billing screen.

Piper is GPL-3 and links espeak-ng, so it is deliberately not imported into this process. It runs as a separate
program that receives one JSON request per line on stdin and answers with one JSON line on stdout; we only ever
hand it text and read back a WAV path. That keeps the ledger proprietary, keeps a crash inside the engine from
taking the app down, and - because the process stays alive - the model is loaded once instead of on every
sentence.

Speaking happens on one worker thread. The UI thread only ever enqueues a sentence and asks, without waiting,
whether an answer has come back.
"""
from __future__ import annotations

import importlib.util
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

SIDECAR_START_TIMEOUT = 25.0
SIDECAR_JOB_TIMEOUT = 30.0


def piper_installed() -> bool:
    """Is the engine present? Checked without importing it - loading onnxruntime costs a second."""
    return importlib.util.find_spec("piper") is not None


def sidecar_command(model: Optional[Path], override: str = "") -> list[str]:
    """argv that starts the sidecar, whether we run from source or from the frozen EXE.

    A voice is either one Piper file or one folder of ONNX graphs; the folder form is answered by the Hindi
    sidecar, and both speak the same JSON-line protocol.
    """
    folder = model is not None and model.is_dir()
    if override:
        head = [override]
    elif getattr(sys, "frozen", False):
        head = [sys.executable, "--speak-hindi" if folder else "--speak"]
    else:
        module = "vx7khata.voice.hindi_sidecar" if folder else "vx7khata.voice.piper_sidecar"
        head = [sys.executable, "-m", module]
    if model is not None:
        head += ["--bundle" if folder else "--model", str(model)]
    return head


def _hidden_flags() -> int:
    """No console window may flash behind the ledger when the EXE speaks (it is built with console=False)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class _Job:
    __slots__ = ("text", "model", "length_scale", "volume", "speaker", "out", "token")

    def __init__(self, text, model, length_scale, volume, speaker, out, token):
        self.text, self.model, self.length_scale = text, model, length_scale
        self.volume, self.speaker, self.out, self.token = volume, speaker, out, token


class SidecarEngine:
    """Owns the speech process. All public methods are safe to call from the GUI thread."""

    def __init__(self, override: str = "", temp_dir: Optional[Path] = None):
        self.override = override
        self.last_error = ""
        self._dir = Path(temp_dir or (Path(tempfile.gettempdir()) / "vx7-voice"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._requests: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._loaded: Optional[str] = None
        self._token = 0
        self._shutdown = False

    # ---- GUI-thread API ---------------------------------------------------
    def start(self, text: str, model: Path, length_scale: float = 1.0, volume: float = 1.0,
              speaker: int = 0) -> str:
        """Queue one sentence and return its token immediately; poll() tells you when the audio is ready."""
        self._token += 1
        token = f"{int(time.time() * 1000)}-{self._token}"
        out = self._dir / f"line-{token}.wav"
        self._ensure_worker()
        self._requests.put(_Job(text, model, length_scale, volume, speaker, out, token))
        return token

    def poll(self) -> Optional[tuple]:
        """(token, Path | None, error) when a finished job has an answer, otherwise None. Never waits."""
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def cancel(self) -> None:
        """Forget everything still queued; a sentence already being spoken is allowed to finish."""
        try:
            while True:
                self._requests.get_nowait()
        except queue.Empty:
            pass

    def shutdown(self) -> None:
        self._shutdown = True
        self._requests.put(None)
        # The worker thread closes the same process when it sees the sentinel, so hold on to the object first:
        # reading self._process twice can hand back None the second time.
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def prune(self, keep: int = 12) -> None:
        try:
            stale = sorted(self._dir.glob("line-*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]
            for path in stale:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    # ---- worker thread ----------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._shutdown or (self._worker and self._worker.is_alive()):
            return
        self._worker = threading.Thread(target=self._run, name="vx7-voice", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            job = self._requests.get()
            if job is None or self._shutdown:
                self._stop_process()
                return
            try:
                self._results.put((job.token, self._speak(job), self.last_error))
            except Exception as exc:  # noqa: BLE001 - speech must never be able to break the ledger
                self.last_error = str(exc)
                self._results.put((job.token, None, self.last_error))

    def _speak(self, job: _Job) -> Optional[Path]:
        if not self._ensure_process(job.model):
            return None
        request = {"text": job.text, "out": str(job.out), "length_scale": job.length_scale,
                   "volume": job.volume, "speaker": job.speaker}
        try:
            assert self._process and self._process.stdin
            self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            assert self._process.stdout
            line = self._process.stdout.readline()
        except (OSError, AssertionError):
            self.last_error = "the speech engine stopped responding"
            self._stop_process()
            return None
        if not line:
            self.last_error = "the speech engine closed unexpectedly"
            self._stop_process()
            return None
        try:
            answer = json.loads(line)
        except ValueError:
            self.last_error = "the speech engine sent an unreadable answer"
            return None
        produced = Path(str(answer.get("out") or ""))
        if not answer.get("ok") or not produced.is_file() or produced.stat().st_size < 1024:
            self.last_error = str(answer.get("error") or "the speech engine produced no audio")
            produced.unlink(missing_ok=True)
            return None
        self.last_error = ""
        return produced

    def _ensure_process(self, model: Path) -> bool:
        key = str(model.resolve()) if model.exists() else ""
        if not key:
            self.last_error = "that voice model is not installed"
            return False
        if self._process and self._process.poll() is None and self._loaded == key:
            return True
        self._stop_process()
        try:
            self._process = subprocess.Popen(
                sidecar_command(model, self.override), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, creationflags=_hidden_flags())
        except FileNotFoundError:
            self.last_error = "the speech engine is not installed"
            return False
        except OSError as exc:
            self.last_error = str(exc)
            return False
        self._loaded = key
        # The first line back is the engine saying it loaded the model; that is the slow part, so it gets its own
        # longer allowance and must not be mistaken for a spoken sentence.
        deadline = time.monotonic() + SIDECAR_START_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self.last_error = "the speech engine could not start"
                return False
            line = self._process.stdout.readline() if self._process.stdout else ""
            if not line:
                # Empty read means the sidecar closed its answer pipe; waiting for the timeout would
                # only leave the worker thread spinning.
                self.last_error = "the speech engine closed unexpectedly"
                self._stop_process()
                return False
            try:
                if json.loads(line).get("ready"):
                    return True
            except ValueError:
                continue
        self.last_error = "the speech engine took too long to start"
        self._stop_process()
        return False

    def _stop_process(self) -> None:
        process, self._process, self._loaded = self._process, None, None
        if not process:
            return
        for pipe in (process.stdin, process.stdout):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


class QtPlayer:
    """Plays a WAV through Qt Multimedia and reports when it has finished.

    ``QSoundEffect`` decodes in the background: after setSource() the status is Loading and play() does nothing
    yet. The manager already polls this player every 120 ms, so the clip is started by whichever poll first
    finds it ready instead of ever waiting for it.
    """

    def __init__(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        self._url = QUrl
        self._ready = QSoundEffect.Status.Ready
        self._broken = QSoundEffect.Status.Error
        self._effect = QSoundEffect()
        self._effect.setLoopCount(0)
        self._waiting = ""
        self._volume = 1.0

    def play(self, path: Path, volume: float) -> bool:
        self._waiting = str(path.resolve())
        self._volume = max(0.0, min(1.0, volume))
        self._effect.setSource(self._url.fromLocalFile(self._waiting))
        # Only a file Qt refuses outright counts as a failure; anything else is still loading.
        return self._effect.status() != self._broken

    def is_playing(self) -> bool:
        if self._waiting:
            status = self._effect.status()
            if status == self._broken:
                self._waiting = ""
                return False
            if status != self._ready:
                return True                      # still decoding: hold the queue, do not overlap
            self._waiting = ""
            self._effect.setVolume(self._volume)
            self._effect.play()
            return True
        return self._effect.isPlaying()

    def stop(self) -> None:
        self._waiting = ""
        self._effect.stop()


class WinsoundPlayer:
    """Last resort on Windows if Qt Multimedia cannot open the audio device.

    winsound cannot tell us whether a clip is still running, so the player keeps its own estimate from the WAV
    header; the queue only uses it to decide when to hand over to the next sentence.
    """

    def __init__(self):
        self._until = 0.0

    def play(self, path: Path, volume: float) -> bool:
        import winsound

        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP)
        except (RuntimeError, OSError):
            return False
        self._until = time.monotonic() + _wav_seconds(path)
        return True

    def is_playing(self) -> bool:
        return time.monotonic() < self._until

    def stop(self) -> None:
        import winsound

        self._until = 0.0
        winsound.PlaySound(None, winsound.SND_PURGE)


def _wav_seconds(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path)) as handle:
            return handle.getnframes() / max(1, handle.getframerate())
    except (OSError, ValueError):
        return 1.5


def make_player():
    """The best player this machine can offer, or None when there is no audio at all."""
    try:
        return QtPlayer()
    except Exception:  # noqa: BLE001 - missing multimedia module or no audio device both land here
        return WinsoundPlayer() if sys.platform == "win32" else None
