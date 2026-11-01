"""SQLite connection, schema and migrations.

* Foreign keys are always enforced.
* Schema changes are applied by numbered migrations, tracked in ``PRAGMA user_version``.
* Each migration runs in one transaction: it fully applies or not at all.
* The connection is in autocommit mode; writes use :func:`transaction`.
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4


class DatabaseError(Exception):
    """The database file cannot be used."""


MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE customers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            name_key   TEXT NOT NULL UNIQUE,
            mobile     TEXT NOT NULL DEFAULT '',
            address    TEXT NOT NULL DEFAULT '',
            notes      TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            txn_date     TEXT NOT NULL
                         CHECK (txn_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            item         TEXT NOT NULL,
            quantity     TEXT,
            rate_paise   INTEGER CHECK (rate_paise IS NULL OR rate_paise > 0),
            amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
            txn_type     TEXT NOT NULL CHECK (txn_type IN ('UDHAAR', 'JAMA')),
            notes        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )""",
        "CREATE INDEX idx_txn_customer_date ON transactions(customer_id, txn_date, id)",
        "CREATE INDEX idx_txn_date ON transactions(txn_date)",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    ],
    2: [
        "CREATE INDEX idx_customers_mobile ON customers(mobile)",
        "CREATE INDEX idx_txn_item ON transactions(item COLLATE NOCASE)",
    ],
    3: [
        # 0 = no credit limit
        "ALTER TABLE customers ADD COLUMN credit_limit_paise INTEGER NOT NULL DEFAULT 0 "
        "CHECK (credit_limit_paise >= 0)",
        # promised payment date for an UDHAAR entry (optional)
        "ALTER TABLE transactions ADD COLUMN due_date TEXT "
        "CHECK (due_date IS NULL OR due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
        "CREATE INDEX idx_txn_due ON transactions(due_date) WHERE due_date IS NOT NULL",
        # Append-only activity trail. Deliberately has no foreign keys so history survives deletions.
        """CREATE TABLE audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            at          TEXT NOT NULL,
            action      TEXT NOT NULL,
            entity      TEXT NOT NULL,
            entity_id   INTEGER,
            customer_id INTEGER,
            summary     TEXT NOT NULL,
            details     TEXT NOT NULL DEFAULT ''
        )""",
        "CREATE INDEX idx_audit_at ON audit_log(at, id)",
        "CREATE INDEX idx_audit_customer ON audit_log(customer_id)",
    ],
    4: [
        # Daily Entry rows wait here for 24 hours (post_at) and are then saved into the ledger automatically.
        # Until then they can be deleted (e.g. the customer returned the goods). They are NOT part of any balance.
        """CREATE TABLE pending_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
            txn_date     TEXT NOT NULL
                         CHECK (txn_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            item         TEXT NOT NULL,
            quantity     TEXT,
            rate_paise   INTEGER CHECK (rate_paise IS NULL OR rate_paise > 0),
            amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
            txn_type     TEXT NOT NULL CHECK (txn_type IN ('UDHAAR', 'JAMA')),
            notes        TEXT NOT NULL DEFAULT '',
            due_date     TEXT
                         CHECK (due_date IS NULL OR due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            created_at   TEXT NOT NULL,
            post_at      TEXT NOT NULL
        )""",
        "CREATE INDEX idx_pending_customer ON pending_entries(customer_id)",
    ],
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "customers": {"id", "name", "name_key", "mobile", "address", "notes", "created_at", "updated_at"},
    "transactions": {
        "id", "customer_id", "txn_date", "item", "quantity", "rate_paise",
        "amount_paise", "txn_type", "notes", "created_at", "updated_at",
    },
    "settings": {"key", "value"},
}

# Columns that only exist from schema v3 on (checked only for backups at v3 or newer).
REQUIRED_COLUMNS_V3: dict[str, set[str]] = {
    "customers": {"credit_limit_paise"},
    "transactions": {"due_date"},
    "audit_log": {"id", "at", "action", "entity", "entity_id", "customer_id", "summary", "details"},
}

# Table that only exists from schema v4 on (checked only for backups at v4 or newer).
REQUIRED_COLUMNS_V4: dict[str, set[str]] = {
    "pending_entries": {
        "id", "customer_id", "txn_date", "item", "quantity", "rate_paise", "amount_paise", "txn_type", "notes",
        "due_date", "created_at", "post_at",
    },
}


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Run a block inside BEGIN IMMEDIATE ... COMMIT; roll back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def repair_missing_objects(conn: sqlite3.Connection) -> None:
    """Re-create schema objects that ``user_version`` claims exist but the file does not have.

    Some older development builds raised user_version without creating every object (seen in the wild:
    version 4 with the v3 columns and audit_log missing). Existing data is never touched.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    customer_cols = _table_columns(conn, "customers") if "customers" in tables else set()
    txn_cols = _table_columns(conn, "transactions") if "transactions" in tables else set()
    with transaction(conn):
        if "idx_customers_mobile" not in indexes:
            conn.execute(MIGRATIONS[2][0])
        if "idx_txn_item" not in indexes:
            conn.execute(MIGRATIONS[2][1])
        if "credit_limit_paise" not in customer_cols:
            conn.execute(MIGRATIONS[3][0])
        if "due_date" not in txn_cols:
            conn.execute(MIGRATIONS[3][1])
        if "idx_txn_due" not in indexes:
            conn.execute(MIGRATIONS[3][2])
        if "audit_log" not in tables:
            for statement in MIGRATIONS[3][3:]:
                conn.execute(statement)
        else:
            if "idx_audit_at" not in indexes:
                conn.execute(MIGRATIONS[3][4])
            if "idx_audit_customer" not in indexes:
                conn.execute(MIGRATIONS[3][5])
        if "pending_entries" not in tables:
            for statement in MIGRATIONS[4]:
                conn.execute(statement)
        elif "idx_pending_customer" not in indexes:
            conn.execute(MIGRATIONS[4][1])


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the schema up to SCHEMA_VERSION. Returns the final version."""
    current = get_user_version(conn)
    if current > SCHEMA_VERSION:
        raise DatabaseError(
            f"This data file was created by a newer version of VX7 KHATA PRO "
            f"(schema {current}, this version supports {SCHEMA_VERSION})."
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        with transaction(conn):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
    return SCHEMA_VERSION


def open_database(path, premigration_backup: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists() and path.stat().st_size > 0

    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise DatabaseError("SQLite foreign key enforcement is unavailable")
        try:
            current = get_user_version(conn)
            tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(f"'{path.name}' is not a valid database file: {exc}") from exc
        if current == 0 and tables > 0:
            raise DatabaseError(f"'{path.name}' is not a VX7 KHATA PRO database")
        if existed and 0 < current < SCHEMA_VERSION and premigration_backup:
            shutil.copyfile(path, path.with_name(f"{path.name}.pre-v{current}-migration.bak"))
        migrate(conn)
        repair_missing_objects(conn)
    except BaseException:
        conn.close()
        raise
    return conn
