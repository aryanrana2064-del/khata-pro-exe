"""Is the backup actually usable - present is not the same as good.

The app already writes backups; this module judges them: how old the newest one is, whether the automatic
schedule ran, and whether the file opens and still holds every customer and entry. Everything here is read-only.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

HEALTHY_HOURS = 30          # one daily cycle plus a little slack for a PC that was switched on late
WARNING_HOURS = 72

HEALTHY = "healthy"
WARNING = "warning"
CRITICAL = "critical"


@dataclass
class Health:
    level: str
    title: str
    detail: str
    newest: Optional[Path]
    age_hours: Optional[float]

    @property
    def ok(self) -> bool:
        return self.level == HEALTHY


def backup_files(folder: Path) -> list[Path]:
    try:
        return sorted((p for p in Path(folder).glob("*.db") if p.is_file()), key=lambda p: p.stat().st_mtime,
                      reverse=True)
    except OSError:
        return []


def newest_backup(service) -> Optional[Path]:
    files = backup_files(service.backups.folder())
    return files[0] if files else None


def assess(service, now: Optional[datetime] = None) -> Health:
    """Age and schedule only - cheap enough to run on every start."""
    now = now or datetime.now()
    status = service.backups.status()
    newest = newest_backup(service)
    if newest is None:
        return Health(CRITICAL, "No backup file",
                      "There is no backup file in the backup folder yet.", None, None)
    try:
        age = max(0.0, (now - datetime.fromtimestamp(newest.stat().st_mtime)).total_seconds() / 3600.0)
    except OSError:
        return Health(CRITICAL, "Backup unreadable", "The newest backup file cannot be read.", newest, None)
    if age <= HEALTHY_HOURS:
        return Health(HEALTHY, "Backup is healthy",
                      f"Newest backup is {age:.1f} hours old.", newest, age)
    if age <= WARNING_HOURS:
        return Health(WARNING, "Backup is getting old",
                      f"Newest backup is {age:.0f} hours old. Make one today.", newest, age)
    note = "" if status.enabled else " Automatic backup is switched off."
    return Health(CRITICAL, "Backup is out of date",
                  f"Newest backup is {age / 24:.1f} days old.{note}", newest, age)


def verify_backup(path: Path, live_counts: Optional[dict] = None) -> tuple[bool, str, dict]:
    """Open the backup read-only and prove it is complete. Returns (ok, message, counts)."""
    path = Path(path)
    if not path.is_file():
        return False, "That backup file does not exist.", {}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        return False, f"The backup cannot be opened: {exc}", {}
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            return False, "The backup failed its integrity check.", {}
        counts = {}
        for table in ("customers", "transactions"):
            try:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                return False, f"The backup has no {table} table.", {}
    except sqlite3.Error as exc:
        return False, f"The backup cannot be read: {exc}", {}
    finally:
        connection.close()
    if live_counts:
        for key, value in live_counts.items():
            if counts.get(key, 0) < int(value):
                return False, (f"The backup has fewer {key} ({counts.get(key, 0)}) than the live data "
                               f"({value}); it is older than the current data."), counts
    return True, f"Backup opens fine: {counts.get('customers', 0)} customers, {counts.get('transactions', 0)} entries.", counts
