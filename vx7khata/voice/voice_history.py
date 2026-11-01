"""The announcement log the settings panel shows.

Same shape as ``backup_history.jsonl``: one JSON object per line, next to the database, so it survives upgrades
and never needs a schema change. Dropped announcements are recorded too - when the assistant stays quiet during
a save, the log is how the shopkeeper (or support) finds out why.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import paths

HISTORY_FILE = "voice_history.jsonl"
MAX_RECORDS = 300


def history_path() -> Path:
    return paths.data_dir() / HISTORY_FILE


def record(event: str, text: str, spoken: bool, reason: str = "") -> None:
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "text": text,
             "spoken": bool(spoken), "reason": reason}
    file = history_path()
    try:
        with file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return
    _trim(file)


def _trim(file: Path) -> None:
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_RECORDS:
            file.write_text("\n".join(lines[-MAX_RECORDS:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def recent(limit: int = 50) -> list[dict]:
    file = history_path()
    if not file.is_file():
        return []
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def clear() -> None:
    try:
        history_path().unlink(missing_ok=True)
    except OSError:
        pass
