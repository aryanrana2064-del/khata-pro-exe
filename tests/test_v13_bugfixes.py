import unittest
import uuid
from datetime import datetime, timedelta

from vx7khata import autobackup, paths
from tests.helpers import ServiceTestCase


class TestBugFixes(unittest.TestCase):
    def test_downloads_known_folder_guid_is_exact(self):
        a, b, c, d = paths._DOWNLOADS_GUID_FIELDS
        built = a.to_bytes(4, "little") + b.to_bytes(2, "little") + c.to_bytes(2, "little") + bytes(d)
        self.assertEqual(built, uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)


class TestHourlyTask(ServiceTestCase):
    def test_task_checks_hourly_and_never_double_backs_up(self):
        xml = autobackup.task_xml(datetime(2026, 10, 9, 23, 0), "x.exe", "--backup")
        self.assertIn("<Interval>PT1H</Interval>", xml)
        self.assertLess(xml.index("<Repetition>"), xml.index("<StartBoundary>"))
        self.svc.backups.run_backup("manual", self.svc.conn)
        self.clock.advance(23 * 3600 + 59 * 60)
        self.assertIsNone(self.svc.backups.run_if_due("scheduled"))   # not due yet: no duplicate
        self.clock.advance(120)
        self.assertIsNotNone(self.svc.backups.run_if_due("scheduled"))  # due the next hourly check


if __name__ == "__main__":
    unittest.main()
