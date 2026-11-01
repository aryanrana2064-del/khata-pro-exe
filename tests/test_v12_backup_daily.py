"""v1.2: Downloads backup folder, 24 h schedule, history, no deletion, task scheduler XML, Daily Udhaar-Jama summary."""
import os
import sqlite3
import xml.dom.minidom as xml_minidom
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tests.helpers import ServiceTestCase
from vx7khata import autobackup, backup, paths
from vx7khata.service import BASIS_ENTERED, BASIS_TXN_DATE, filter_daily_rows


class TestBackupV12(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")
        self.add(self.c.id, "UDHAAR", "03-10-2026", "Chain", "1000")
        self.bm = self.svc.backups

    def test_default_folder_is_downloads_sjb_khata_backup_24(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VX7_BACKUP_DIR", None)
            with mock.patch.object(paths, "downloads_dir", return_value=Path("/x/Downloads")):
                self.assertEqual(autobackup.default_backup_dir(), Path("/x/Downloads/SJB KHATA BACKUP 24"))

    def test_folder_created_and_filename_format(self):
        self.assertFalse(os.path.isdir(self.backup_dir))
        p = self.bm.run_backup("manual", self.svc.conn)
        self.assertTrue(os.path.isdir(self.backup_dir))
        self.assertRegex(p.name, r"^SJB_KHATA_BACKUP_\d{4}-\d{2}-\d{2}_\d{6}\.db$")
        self.assertEqual(backup.validate_backup(p).transactions, 1)

    def test_never_overwrites_same_second(self):
        a = self.bm.run_backup("manual", self.svc.conn)
        b = self.bm.run_backup("manual", self.svc.conn)
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())

    def test_24h_schedule_and_no_duplicate_after_restart(self):
        self.assertIsNotNone(self.svc.auto_backup_if_due())
        self.clock.advance(23 * 3600)
        self.assertIsNone(self.svc.auto_backup_if_due())
        fresh = autobackup.BackupManager(self.db_file, clock=lambda: self.clock.now)   # "restart"
        self.assertIsNone(fresh.run_if_due("auto"))
        self.clock.advance(3600 + 1)
        self.assertIsNotNone(self.svc.auto_backup_if_due())
        self.assertEqual(self.bm.status().backups_in_folder, 2)

    def test_old_backups_are_never_deleted(self):
        for _ in range(20):
            self.clock.advance(25 * 3600)
            self.svc.auto_backup_if_due()
        self.assertEqual(len(list(Path(self.backup_dir).glob("SJB_KHATA_BACKUP_*.db"))), 20)

    def test_disable_auto_keeps_manual(self):
        self.bm.set_enabled(False)
        self.assertIsNone(self.svc.auto_backup_if_due())
        self.assertIsNone(self.bm.status().next_due)
        self.assertTrue(self.svc.auto_backup_if_due(force=True).exists())

    def test_status_history_and_failure_message(self):
        self.svc.auto_backup_if_due(force=True)
        st = self.bm.status()
        self.assertEqual(st.status, "Success")
        self.assertIsNotNone(st.last_success)
        self.assertEqual(st.next_due, st.last_success + timedelta(hours=24))
        blocker = Path(self.tmp.name) / "afile"
        blocker.write_text("x")
        self.bm.set_folder(str(blocker / "sub"))          # impossible folder
        with self.assertRaises(backup.BackupError) as cm:
            self.bm.run_backup("manual", self.svc.conn)
        self.assertIn("Cannot write to the backup folder", str(cm.exception))
        self.assertEqual(self.bm.status().status, "Failed")
        hist = self.bm.history()
        self.assertEqual([h["ok"] for h in hist], [False, True])

    def test_live_database_untouched_by_backup(self):
        before = Path(self.db_file).read_bytes()
        self.bm.run_backup("manual")                      # headless path: separate read-only connection
        self.assertEqual(Path(self.db_file).read_bytes(), before)

    def test_headless_backup_while_app_connection_open(self):
        p = self.bm.run_backup("scheduled")
        conn = sqlite3.connect(p)
        self.assertEqual(conn.execute("select count(*) from transactions").fetchone()[0], 1)
        conn.close()

    def test_restore_roundtrip_and_corrupt_rejected(self):
        p = self.svc.auto_backup_if_due(force=True)
        self.add(self.c.id, "JAMA", "03-10-2026", "Cash", "300")
        self.assertEqual(self.svc.customer_balance(self.c.id), 70000)
        safety = self.svc.restore_backup(p)
        self.assertTrue(safety.exists())
        self.assertEqual(self.svc.customer_balance(self.c.id), 100000)
        bad = Path(self.tmp.name) / "bad.db"
        bad.write_bytes(b"not a database at all")
        with self.assertRaises(backup.BackupError):
            self.svc.restore_backup(bad)
        self.assertEqual(self.svc.customer_balance(self.c.id), 100000)

    def test_task_xml_is_valid_and_catches_up_missed_runs(self):
        xml = autobackup.task_xml(datetime(2026, 10, 9, 23, 0), r"C:\Program Files\VX7 KHATA PRO\VX7 KHATA PRO.exe", "--backup")
        parsed = xml_minidom.parseString(xml.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode())
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", xml)
        self.assertIn("<DaysInterval>1</DaysInterval>", xml)
        self.assertIn("--backup", xml)
        self.assertTrue(parsed)

    def test_install_task_calls_schtasks(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(autobackup, "_is_windows", return_value=True):
            ok, _ = autobackup.install_task(runner=fake)
        self.assertTrue(ok)
        self.assertEqual(calls[0][:4], ["schtasks", "/Create", "/TN", autobackup.TASK_NAME])


class TestDailySummary(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.svc.add_customer("Amit")
        self.b = self.svc.add_customer("Bharat")
        self.add(self.a.id, "UDHAAR", "03-10-2026", "Chain", "5000")
        self.add(self.a.id, "JAMA", "03-10-2026", "Cash", "1000")
        self.add(self.b.id, "UDHAAR", "03-10-2026", "Ring", "2500")
        self.add(self.b.id, "JAMA", "02-10-2026", "Old payment", "700")     # back-dated

    def test_stats_for_day(self):
        s = self.svc.daily_summary("2026-10-03")
        self.assertEqual((s.total_udhaar, s.total_jama), (750000, 100000))
        self.assertEqual(s.net_difference, -650000)
        self.assertEqual((s.transaction_count, s.customer_count), (3, 2))

    def test_other_date_and_default_today(self):
        self.assertEqual(self.svc.daily_summary("2026-10-02").total_jama, 70000)
        self.assertEqual(self.svc.daily_summary().transaction_count, 3)      # clock is 2026-10-03
        self.assertEqual(self.svc.daily_summary("2026-09-01").transaction_count, 0)

    def test_entered_basis_includes_backdated(self):
        s = self.svc.daily_summary("2026-10-03", BASIS_ENTERED)
        self.assertEqual(s.transaction_count, 4)

    def test_updates_after_edit_and_delete_no_double_count(self):
        first = self.svc.daily_summary("2026-10-03").rows[0]
        self.svc.delete_transaction(first.txn_id)
        s = self.svc.daily_summary("2026-10-03")
        self.assertEqual((s.transaction_count, s.total_udhaar), (2, 250000))
        ids = [r.txn_id for r in s.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_customer_balance_differs_from_daily_figures(self):
        self.assertEqual(self.svc.customer_balance(self.a.id), 400000)
        self.assertEqual(self.svc.daily_summary("2026-10-02").total_udhaar, 0)

    def test_filter_search_sort(self):
        rows = self.svc.daily_summary("2026-10-03").rows
        self.assertEqual(len(filter_daily_rows(rows, "JAMA")), 1)
        self.assertEqual({r.customer_name for r in filter_daily_rows(rows, "UDHAAR")}, {"Amit", "Bharat"})
        self.assertEqual([r.customer_name for r in filter_daily_rows(rows, search="bha")], ["Bharat"])
        self.assertEqual(filter_daily_rows(rows, sort="amount", descending=True)[0].amount_paise, 500000)
        self.assertEqual(filter_daily_rows(rows, sort="name")[0].customer_name, "Amit")
