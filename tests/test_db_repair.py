"""A database whose user_version lies (claims v4 but is missing the v3 objects) must be repaired, not crash."""
import os
import shutil
import sqlite3
import tempfile
import unittest

from vx7khata import db as dbmod


class TestSchemaRepair(unittest.TestCase):
    def _make_broken_db(self, path):
        """Simulate the old dev build: migrations 1 + 4 applied, user_version 4, v3 objects missing."""
        conn = sqlite3.connect(path)
        for statement in dbmod.MIGRATIONS[1]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.execute(dbmod.MIGRATIONS[4][0])          # pending_entries exists (v4 partly applied)
        conn.execute(dbmod.MIGRATIONS[4][1])
        conn.execute("INSERT INTO customers (name, name_key, created_at, updated_at) "
                     "VALUES ('Rahul', 'rahul', '2026-10-01T10:00:00', '2026-10-01T10:00:00')")
        conn.commit()
        conn.close()

    def _tmp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)   # runs AFTER the conn.close cleanups (LIFO)
        return tmp

    def test_open_database_repairs_missing_v3_objects(self):
        tmp = self._tmp()
        path = os.path.join(tmp, "khata.db")
        self._make_broken_db(path)
        conn = dbmod.open_database(path, premigration_backup=False)
        self.addCleanup(conn.close)
        cols = dbmod._table_columns(conn, "customers")
        self.assertIn("credit_limit_paise", cols)
        self.assertIn("due_date", dbmod._table_columns(conn, "transactions"))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("audit_log", tables)
        self.assertIn("pending_entries", tables)
        # data survived untouched
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT credit_limit_paise FROM customers").fetchone()[0], 0)

    def test_open_database_repair_is_idempotent(self):
        tmp = self._tmp()
        path = os.path.join(tmp, "khata.db")
        conn = dbmod.open_database(path)
        conn.close()
        conn = dbmod.open_database(path)            # second open must not fail or duplicate anything
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
