"""Backup creation and validation."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db as dbmod

SQLITE_MAGIC = b"SQLite format 3\x00"


class BackupError(Exception):
    """A backup could not be created, or a backup file is not acceptable."""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    schema_version: int
    customers: int
    transactions: int
    size_bytes: int


def create_backup(src_conn: sqlite3.Connection, dest_path) -> Path:
    """Write a consistent copy of the live database to ``dest_path`` and verify it."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.unlink(missing_ok=True)
    try:
        target = sqlite3.connect(str(tmp))
        try:
            src_conn.backup(target)  # SQLite online-backup API: safe while the app is open
        finally:
            target.close()
        validate_backup(tmp)
        os.replace(tmp, dest)
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise BackupError(f"Could not create the backup: {exc}") from exc
    return dest


def validate_backup(path) -> BackupInfo:
    """Raise BackupError unless ``path`` is an intact, compatible VX7 KHATA PRO database."""
    p = Path(path)
    if not p.is_file():
        raise BackupError("Backup file does not exist")
    size = p.stat().st_size
    if size == 0:
        raise BackupError("Backup file is empty")
    with open(p, "rb") as fh:
        if fh.read(16) != SQLITE_MAGIC:
            raise BackupError("This is not a VX7 KHATA PRO backup (not a SQLite database)")

    conn = None
    try:
        conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        if integrity != ["ok"]:
            raise BackupError("Backup is damaged: " + "; ".join(str(i) for i in integrity[:3]))
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1:
            raise BackupError("This is not a VX7 KHATA PRO backup")
        if version > dbmod.SCHEMA_VERSION:
            raise BackupError(
                f"Backup was made by a newer version of the app (schema {version}); please update the app first"
            )
        for table, needed in dbmod.REQUIRED_COLUMNS.items():
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not cols:
                raise BackupError(f"Backup is missing the '{table}' table")
            if not needed <= cols:
                raise BackupError(f"Backup table '{table}' is missing columns: {', '.join(sorted(needed - cols))}")
        if version >= 3:
            for table, needed in dbmod.REQUIRED_COLUMNS_V3.items():
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if not needed <= cols:
                    raise BackupError(
                        f"Backup table '{table}' is missing columns: {', '.join(sorted(needed - cols))}"
                    )
        if version >= 4:
            for table, needed in dbmod.REQUIRED_COLUMNS_V4.items():
                cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if not needed <= cols:
                    raise BackupError(
                        f"Backup table '{table}' is missing columns: {', '.join(sorted(needed - cols)) or 'all'}"
                    )
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BackupError("Backup has entries that point to missing customers")
        bad = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE amount_paise <= 0 OR txn_type NOT IN ('UDHAAR','JAMA') "
            "OR txn_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
        ).fetchone()[0]
        if bad:
            raise BackupError(f"Backup contains {bad} invalid entries")
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(f"Backup cannot be read: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    return BackupInfo(p, version, customers, transactions, size)
