"""Shared test helpers."""
import os
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from vx7khata.service import KhataService


class FakeClock:
    """A controllable clock so tests do not depend on the real time."""

    def __init__(self, start=None):
        self.now = start or datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_file = os.path.join(self.tmp.name, "khata.db")
        self.backup_dir = os.path.join(self.tmp.name, "SJB KHATA BACKUP 24")   # created by the first backup
        env = mock.patch.dict(os.environ, {"VX7_BACKUP_DIR": self.backup_dir})
        env.start()
        self.addCleanup(env.stop)
        self.clock = FakeClock()
        self.svc = KhataService(self.db_file, clock=self.clock)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.svc.close()
        except Exception:
            pass

    def add(self, customer_id, kind, date, item, amount=None, **kw):
        """Add an entry; advance the clock so the duplicate guard never interferes."""
        self.clock.advance(60)
        return self.svc.add_transaction(customer_id, kind, date, item, amount=amount, **kw)
