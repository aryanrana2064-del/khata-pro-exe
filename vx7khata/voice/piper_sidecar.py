"""The speech sidecar - a separate program, on purpose.

Piper is GPL-3 and links espeak-ng, so it must not be linked into the proprietary ledger process. This file is
the whole boundary: it is started as its own program, speaks one sentence per request, and never sees the
database, the customers or anything else the app holds.

Protocol (JSON lines, one request in, one answer out):

    ->  {"ready": true}                                  sent once the model has loaded
    <-  {"text": …, "out": …, "length_scale": …, …}      a sentence to speak
    ->  {"ok": true, "out": "…wav"}  or  {"ok": false, "error": "…"}

Run from source with ``python -m vx7khata.voice.piper_sidecar --model <file.onnx>``; the frozen EXE reaches the
same code with ``"VX7 KHATA PRO.exe" --speak --model <file.onnx>``.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path


def _answer(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _open_pipes() -> None:
    """Put stdin/stdout on UTF-8 whatever the console-less EXE handed us.

    The shipped program is built without a console, so Python may leave these as None or - worse - as text
    streams in the system code page with ``surrogateescape``. Hindi then arrives as lone surrogates and the
    engine reports a cast error instead of speaking. Re-opening descriptors 0 and 1 as UTF-8 fixes both cases;
    ``closefd=False`` keeps the pipes themselves owned by the parent process.
    """
    import os

    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w")):
        current = getattr(sys, name, None)
        encoding = (getattr(current, "encoding", "") or "").lower().replace("-", "")
        if current is not None and encoding == "utf8":
            continue
        try:
            setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8", errors="strict",
                                        buffering=1 if name == "stdout" else -1, closefd=False))
        except OSError:
            pass


def _espeak_data_dir() -> str:
    """Where espeak-ng's sound tables live for this build, or "" to let piper decide.

    Piper defaults to the folder inside its own package, which is right for a pip install and wrong inside the
    frozen EXE, where PyInstaller unpacks that folder next to the executable instead.
    """
    base = getattr(sys, "_MEIPASS", "")
    if base:
        candidate = Path(base) / "piper" / "espeak-ng-data"
        if candidate.is_dir():
            return str(candidate)
    return ""


def main(argv: list[str] | None = None) -> int:
    _open_pipes()
    parser = argparse.ArgumentParser(description="Offline speech sidecar for VX7 KHATA PRO")
    parser.add_argument("--model", default="", help="path to a .onnx voice model")
    args = parser.parse_args(argv)

    try:
        from piper import PiperVoice, SynthesisConfig
    except ImportError as exc:   # the engine was not bundled or installed
        _answer({"ready": False, "error": f"piper is unavailable: {exc}"})
        return 3

    voice = None
    loaded = ""
    data_dir = _espeak_data_dir()
    extra = {"espeak_data_dir": data_dir} if data_dir else {}

    def _load(path: str):
        return PiperVoice.load(path, **extra)

    if args.model:
        try:
            voice = _load(args.model)
            loaded = args.model
        except Exception as exc:  # noqa: BLE001 - report, never traceback into the parent's stdout
            _answer({"ready": False, "error": f"could not load the voice model: {exc}"})
            return 4
    _answer({"ready": True, "model": loaded})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _answer({"ok": False, "error": "unreadable request"})
            continue
        model = str(request.get("model") or "")
        if model and model != loaded:            # the customer changed voice in Settings
            try:
                voice = _load(model)
                loaded = model
            except Exception as exc:  # noqa: BLE001
                _answer({"ok": False, "error": f"could not load the voice model: {exc}"})
                continue
        if voice is None:
            _answer({"ok": False, "error": "no voice model was given"})
            continue
        out = Path(str(request.get("out") or ""))
        if not out.parent.is_dir():
            _answer({"ok": False, "error": "the output folder does not exist"})
            continue
        try:
            config = SynthesisConfig(
                length_scale=float(request.get("length_scale") or 1.0),
                volume=float(request.get("volume", 1.0)),
            )
            speaker = request.get("speaker")
            if speaker is not None:
                config.speaker_id = int(speaker)
            with wave.open(str(out), "wb") as handle:
                voice.synthesize_wav(str(request.get("text") or ""), handle, syn_config=config)
        except Exception as exc:  # noqa: BLE001 - one bad sentence must not end the session
            out.unlink(missing_ok=True)
            _answer({"ok": False, "error": str(exc)})
            continue
        _answer({"ok": True, "out": str(out)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
