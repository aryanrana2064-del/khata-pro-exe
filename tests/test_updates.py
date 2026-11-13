"""Online update check: version maths, release parsing, checksum and the after-update data proof."""
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from tests.helpers import ServiceTestCase
from vx7khata import __version__, updates


class TestVersions(unittest.TestCase):
    def test_plain_and_v_prefixed_versions_compare(self):
        self.assertTrue(updates.is_newer("v1.3.1", "1.3.0"))
        self.assertFalse(updates.is_newer("1.3.0", "1.3.0"))
        self.assertFalse(updates.is_newer("v1.2.9", "1.3.0"))

    def test_numbers_compare_as_numbers_not_text(self):
        self.assertTrue(updates.is_newer("1.10.0", "1.9.9"))
        self.assertTrue(updates.is_newer("2.0", "1.99.99"))

    def test_junk_never_wins(self):
        self.assertEqual(updates.version_tuple(""), (0,))
        self.assertEqual(updates.version_tuple("vNext"), (0,))
        self.assertFalse(updates.is_newer("", __version__))


class TestReleaseParsing(unittest.TestCase):
    def payload(self, **overrides):
        base = {
            "tag_name": "v1.3.2",
            "body": "Fixes and a faster ledger.",
            "html_url": "https://example.invalid/release",
            "assets": [
                {"name": "VX7-KHATA-PRO-1.3.2-windows.zip", "browser_download_url": "https://e/x.zip"},
                {"name": "VX7-KHATA-PRO-Setup-1.3.2.exe", "browser_download_url": "https://e/setup.exe",
                 "digest": "sha256:" + "a" * 64, "size": 123},
            ],
        }
        base.update(overrides)
        return base

    def test_the_installer_is_chosen_over_the_portable_zip(self):
        release = updates.parse_release(self.payload())
        self.assertEqual(release.version, "1.3.2")
        self.assertEqual(release.installer_url, "https://e/setup.exe")
        self.assertEqual(release.sha256, "a" * 64)
        self.assertEqual(release.size, 123)
        self.assertIn("faster ledger", release.notes)

    def test_a_version_json_with_direct_fields_also_parses(self):
        release = updates.parse_release({"version": "1.4.0", "installer_url": "https://e/i.exe",
                                         "sha256": "b" * 64, "notes": "Hello"})
        self.assertEqual((release.version, release.installer_url, release.notes),
                         ("1.4.0", "https://e/i.exe", "Hello"))

    def test_a_release_without_an_installer_is_ignored(self):
        self.assertIsNone(updates.parse_release({"tag_name": "v9", "assets": []}))
        self.assertIsNone(updates.parse_release({}))

    def test_a_checksums_file_is_understood(self):
        table = updates.parse_checksums(f"{'a' * 64}  VX7-Setup.exe\nnot a checksum line\n{'b' * 64} *other.zip\n")
        self.assertEqual(table["VX7-Setup.exe"], "a" * 64)
        self.assertEqual(table["other.zip"], "b" * 64)

    def test_the_sums_file_supplies_the_checksum_when_the_api_has_none(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return f"{'c' * 64}  setup.exe\n".encode()

        payload = {"assets": [{"name": "SHA256SUMS", "browser_download_url": "https://e/SHA256SUMS"}]}
        with mock.patch("urllib.request.urlopen", return_value=Response()):
            found = updates._checksum_from_assets(payload, "setup.exe", 5)
        self.assertEqual(found, "c" * 64)

    def test_an_offline_pc_gets_a_sentence_not_a_traceback(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(updates.UpdateError) as caught:
                updates.fetch_release()
        self.assertIn("No internet", str(caught.exception))

    def test_a_missing_release_page_says_so(self):
        error = urllib.error.HTTPError("https://e", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(updates.UpdateError) as caught:
                updates.fetch_release()
        self.assertIn("No update", str(caught.exception))


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder / "installer.exe"
        self.source.write_bytes(b"installer-bytes" * 1000)
        self.release = updates.Release(version="1.3.2", notes="", installer_url=self.source.as_uri())

    def test_a_matching_checksum_is_kept(self):
        self.release.sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        target = updates.download(self.release, self.folder / "downloads")
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_a_wrong_checksum_is_deleted_not_installed(self):
        self.release.sha256 = "0" * 64
        with self.assertRaises(updates.UpdateError):
            updates.download(self.release, self.folder / "downloads")
        self.assertEqual(list((self.folder / "downloads").glob("*.exe")), [])

    def test_progress_reaches_the_end(self):
        seen = []
        self.release.sha256 = hashlib.sha256(self.source.read_bytes()).hexdigest()
        updates.download(self.release, self.folder / "downloads", progress=lambda got, total: seen.append((got, total)))
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], self.source.stat().st_size)


class TestDataProof(ServiceTestCase):
    """The numbers checked after an update: same rows, or the pre-update backup is offered back."""

    def test_a_healthy_database_matches_its_snapshot(self):
        self.svc.add_customer("Ramesh")
        snapshot = updates.data_snapshot(self.svc)
        ok, detail = updates.verify_data(self.svc, snapshot)
        self.assertTrue(ok, detail)

    def test_a_database_that_lost_rows_fails_the_check(self):
        self.svc.add_customer("Ramesh")
        snapshot = updates.data_snapshot(self.svc)
        snapshot["transactions"] += 1
        ok, detail = updates.verify_data(self.svc, snapshot)
        self.assertFalse(ok)
        self.assertIn("fell", detail)

    def test_an_empty_snapshot_is_not_an_excuse_to_skip_the_check(self):
        ok, detail = updates.verify_data(self.svc, {})
        self.assertTrue(ok, detail)


class TestUpdatePanel(ServiceTestCase):
    """The Settings card itself: present, switchable, and honest about what it sends."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from vx7khata.ui.main_window import MainWindow

        window = MainWindow(self.svc)
        self.addCleanup(window.deleteLater)
        window.show_page("settings")
        return window

    def test_the_settings_page_has_the_update_card(self):
        window = self.window()
        self.assertIn(f"v{__version__}", window.update_status.text())
        self.assertTrue(window.update_auto.isChecked())
        self.assertFalse(window.update_install_button.isVisible())

    def test_switching_automatic_checks_off_is_remembered(self):
        window = self.window()
        window.update_auto.setChecked(False)
        self.assertEqual(self.svc.get_setting("update.auto"), "0")
        self.assertFalse(window._update_auto_enabled())
        window.update_auto.setChecked(True)
        self.assertTrue(window._update_auto_enabled())

    def test_an_installer_that_never_ran_leaves_no_marker_behind(self):
        self.svc.set_setting("update.pending_version", "9.9.9")
        self.svc.set_setting("update.pending_from", __version__)
        window = self.window()
        window._finish_pending_update()
        self.assertEqual(self.svc.get_setting("update.pending_version"), "")

    def test_a_finished_update_with_healthy_data_clears_the_marker(self):
        snapshot = updates.data_snapshot(self.svc)
        self.svc.set_setting("update.pending_version", __version__)
        self.svc.set_setting("update.pending_from", "1.3.0")
        self.svc.set_setting("update.pending_counts", json.dumps(snapshot))
        window = self.window()
        window._finish_pending_update()
        self.assertEqual(self.svc.get_setting("update.pending_version"), "")
