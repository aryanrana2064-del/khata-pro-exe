"""Automatic copy of finished backups into a cloud-synced folder (Google Drive / OneDrive / Dropbox)."""
import os
import shutil
from pathlib import Path
from unittest import mock

from tests.helpers import ServiceTestCase


class TestCloudBackup(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.mgr = self.svc.backups
        self.cloud = Path(self.tmp.name) / "GoogleDrive" / "VX7 Backup"

    def test_off_by_default(self):
        st = self.mgr.cloud_status()
        self.assertFalse(st.configured)
        self.svc.auto_backup_if_due(force=True)
        self.assertFalse(self.cloud.exists())

    def test_backup_is_copied_to_cloud_folder(self):
        self.cloud.parent.mkdir()
        self.mgr.set_cloud_folder(str(self.cloud))
        path = self.svc.auto_backup_if_due(force=True)
        self.assertTrue((self.cloud / path.name).is_file())
        st = self.mgr.cloud_status()
        self.assertTrue(st.available)
        self.assertEqual((st.pending, st.error), (0, ""))
        self.assertIsNotNone(st.last_success)
        self.assertFalse(list(self.cloud.glob("*.part")))

    def test_offline_then_retry(self):
        self.mgr.set_cloud_folder(str(self.cloud))            # parent folder (the drive) does not exist yet
        path = self.svc.auto_backup_if_due(force=True)        # local backup still succeeds
        self.assertTrue(path.is_file())
        st = self.mgr.cloud_status()
        self.assertFalse(st.available)
        self.assertEqual(st.pending, 1)
        self.assertIn("cloud folder", st.error.lower())
        self.cloud.parent.mkdir()                             # Google Drive starts / comes online
        self.assertEqual(self.mgr.sync_cloud(), 1)
        self.assertTrue((self.cloud / path.name).is_file())
        self.assertEqual(self.mgr.cloud_status().pending, 0)
        self.assertEqual(self.mgr.cloud_status().error, "")

    def test_timer_retries_when_nothing_is_due(self):
        self.svc.auto_backup_if_due(force=True)               # a local backup exists and is not due again
        self.cloud.parent.mkdir()
        self.mgr.set_cloud_folder(str(self.cloud))
        self.assertIsNone(self.svc.auto_backup_if_due())      # not due, but the missing cloud copy is made
        self.assertEqual(len(list(self.cloud.glob("SJB_KHATA_BACKUP_*.db"))), 1)

    def test_turning_it_off_and_old_files_are_kept(self):
        self.cloud.parent.mkdir()
        self.mgr.set_cloud_folder(str(self.cloud))
        path = self.svc.auto_backup_if_due(force=True)
        self.mgr.set_cloud_folder(None)
        self.assertFalse(self.mgr.cloud_status().configured)
        self.assertTrue((self.cloud / path.name).is_file())   # nothing is deleted from the cloud folder


class TestMultiCloudFolders(ServiceTestCase):
    """OneDrive AND Google Drive at the same time, plus the one-time automatic setup."""

    def setUp(self):
        super().setUp()
        self.mgr = self.svc.backups
        self.od = Path(self.tmp.name) / "OneDrive" / "SJB KHATA BACKUP 24"
        self.gd = Path(self.tmp.name) / "GoogleDrive" / "SJB KHATA BACKUP 24"
        self.od.parent.mkdir()
        self.gd.parent.mkdir()

    def test_both_folders_receive_every_backup(self):
        self.assertTrue(self.mgr.add_cloud_folder(self.od))
        self.assertTrue(self.mgr.add_cloud_folder(self.gd))
        self.assertFalse(self.mgr.add_cloud_folder(self.od))    # adding twice does nothing
        path = self.svc.auto_backup_if_due(force=True)
        self.assertTrue((self.od / path.name).is_file())
        self.assertTrue((self.gd / path.name).is_file())
        st = self.mgr.cloud_status()
        self.assertTrue(st.configured and st.available)
        self.assertEqual((st.pending, st.error), (0, ""))
        self.assertEqual(self.mgr.cloud_folders(), [self.od, self.gd])

    def test_one_folder_offline_does_not_stop_the_other(self):
        self.mgr.add_cloud_folder(self.od)
        self.mgr.add_cloud_folder(self.gd)
        shutil.rmtree(self.gd.parent)                           # Google Drive "disappears"
        path = self.svc.auto_backup_if_due(force=True)
        self.assertTrue((self.od / path.name).is_file())        # OneDrive still got the copy
        st = self.mgr.cloud_status()
        self.assertFalse(st.available)
        self.assertIn("not found", st.error)
        self.gd.parent.mkdir()
        self.assertEqual(self.mgr.sync_cloud(), 1)              # retry copies only the missing one
        self.assertTrue((self.gd / path.name).is_file())
        self.assertEqual(self.mgr.cloud_status().pending, 0)

    def test_remove_one_folder_keeps_the_other(self):
        self.mgr.add_cloud_folder(self.od)
        self.mgr.add_cloud_folder(self.gd)
        self.mgr.remove_cloud_folder(self.od)
        self.assertEqual(self.mgr.cloud_folders(), [self.gd])
        path = self.svc.auto_backup_if_due(force=True)
        self.assertTrue((self.gd / path.name).is_file())

    def test_legacy_single_folder_state_is_kept(self):
        self.mgr.set_cloud_folder(str(self.od))                 # old-style single folder
        self.mgr.add_cloud_folder(self.gd)                      # migrate to the list
        self.assertEqual(self.mgr.cloud_folders(), [self.od, self.gd])
        path = self.svc.auto_backup_if_due(force=True)
        self.assertTrue((self.od / path.name).is_file())
        self.assertTrue((self.gd / path.name).is_file())

    def test_auto_setup_adds_detected_apps_once(self):
        # auto-setup is skipped while the backup-dir override or the headless test platform is set
        with mock.patch.dict(os.environ, {"VX7_BACKUP_DIR": "", "QT_QPA_PLATFORM": ""}):
            with mock.patch("vx7khata.autobackup.detect_cloud_folders",
                            return_value={"OneDrive": self.od, "Google Drive": self.gd}), \
                 mock.patch.object(self.mgr, "sync_cloud", return_value=0) as sync:
                added = self.mgr.auto_cloud_setup()
                self.assertEqual(added, [self.od, self.gd])
                sync.assert_called_once()
                self.assertEqual(self.mgr.auto_cloud_setup(), [])   # never re-adds behind the user's back
            self.mgr.remove_cloud_folder(self.od)
            with mock.patch("vx7khata.autobackup.detect_cloud_folders",
                            return_value={"OneDrive": self.od, "Google Drive": None}):
                self.assertEqual(self.mgr.auto_cloud_setup(), [])   # user turned it off: stays off

    def test_auto_setup_skipped_with_backup_dir_override(self):
        with mock.patch("vx7khata.autobackup.detect_cloud_folders",
                        return_value={"OneDrive": self.od, "Google Drive": self.gd}):
            self.assertEqual(self.mgr.auto_cloud_setup(), [])   # VX7_BACKUP_DIR is set by the test harness
        self.assertEqual(self.mgr.cloud_folders(), [])
