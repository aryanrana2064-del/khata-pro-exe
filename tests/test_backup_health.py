"""Backup health: age, schedule, and the read-only proof that a backup still holds the data."""
import os
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tests.helpers import ServiceTestCase
from vx7khata import backup_health


class TestAssess(ServiceTestCase):
    def test_no_backup_at_all_is_critical(self):
        health = backup_health.assess(self.svc)
        self.assertEqual(health.level, backup_health.CRITICAL)
        self.assertIsNone(health.newest)

    def test_a_fresh_backup_is_healthy(self):
        self.svc.add_customer("Ramesh")
        self.svc.auto_backup_if_due(force=True)
        health = backup_health.assess(self.svc)
        self.assertTrue(health.ok, health.detail)
        self.assertLess(health.age_hours, backup_health.HEALTHY_HOURS)

    def test_a_backup_older_than_a_day_is_a_warning(self):
        self.svc.auto_backup_if_due(force=True)
        newest = backup_health.newest_backup(self.svc)
        old = time.time() - 50 * 3600
        os.utime(newest, (old, old))
        self.assertEqual(backup_health.assess(self.svc).level, backup_health.WARNING)

    def test_a_week_old_backup_is_critical(self):
        self.svc.auto_backup_if_due(force=True)
        newest = backup_health.newest_backup(self.svc)
        old = time.time() - 8 * 24 * 3600
        os.utime(newest, (old, old))
        health = backup_health.assess(self.svc)
        self.assertEqual(health.level, backup_health.CRITICAL)
        self.assertIn("days old", health.detail)


class TestVerify(ServiceTestCase):
    def test_a_real_backup_opens_and_counts(self):
        self.svc.add_customer("Ramesh")
        self.svc.add_customer("Suresh")
        self.svc.auto_backup_if_due(force=True)
        newest = backup_health.newest_backup(self.svc)
        ok, message, counts = backup_health.verify_backup(newest)
        self.assertTrue(ok, message)
        self.assertEqual(counts["customers"], 2)

    def test_a_backup_older_than_the_live_data_is_reported(self):
        self.svc.add_customer("Ramesh")
        self.svc.auto_backup_if_due(force=True)
        newest = backup_health.newest_backup(self.svc)
        ok, message, _counts = backup_health.verify_backup(newest, {"customers": 5})
        self.assertFalse(ok)
        self.assertIn("fewer customers", message)

    def test_a_corrupt_file_fails_instead_of_crashing(self):
        broken = Path(self.tmp.name) / "broken.db"
        broken.write_bytes(b"this is not a database at all" * 100)
        ok, message, _counts = backup_health.verify_backup(broken)
        self.assertFalse(ok)
        self.assertTrue(message)

    def test_a_missing_file_is_reported(self):
        ok, message, _counts = backup_health.verify_backup(Path(self.tmp.name) / "nope.db")
        self.assertFalse(ok)
        self.assertIn("does not exist", message)