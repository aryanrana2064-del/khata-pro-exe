import os
import tempfile
import unittest

from vx7khata import pagesetup
from vx7khata.service import KhataService, ValidationError


class PageSetupTests(unittest.TestCase):
    def setUp(self):
        self.svc = KhataService(os.path.join(tempfile.mkdtemp(), "k.db"))

    def test_default_is_a4_portrait(self):
        s = pagesetup.load(self.svc)
        self.assertEqual((s.size, s.orientation), ("A4", "portrait"))

    def test_a5_landscape_is_remembered(self):
        pagesetup.save(self.svc, pagesetup.PageSetup("A5", pagesetup.LANDSCAPE))
        s = pagesetup.load(self.svc)
        self.assertEqual((s.size, s.orientation), ("A5", "landscape"))

    def test_custom_size_is_remembered(self):
        pagesetup.save(self.svc, pagesetup.PageSetup(pagesetup.CUSTOM, "portrait", 148.5, 210))
        s = pagesetup.load(self.svc)
        self.assertTrue(s.is_custom)
        self.assertEqual((s.custom_w_mm, s.custom_h_mm), (148.5, 210.0))

    def test_custom_size_out_of_range_is_rejected(self):
        for w, h in ((10, 200), (200, 5000)):
            with self.assertRaises(ValidationError):
                pagesetup.save(self.svc, pagesetup.PageSetup(pagesetup.CUSTOM, "portrait", w, h))

    def test_damaged_saved_values_fall_back_to_default(self):
        self.svc.set_setting("print_custom_w", "abc")
        self.assertEqual(pagesetup.load(self.svc), pagesetup.PageSetup())


class PrintItemsTests(unittest.TestCase):
    def setUp(self):
        from vx7khata import exports
        self.exports = exports
        self.svc = KhataService(os.path.join(tempfile.mkdtemp(), "k.db"))
        c = self.svc.add_customer("Asha", mobile="9876543210", address="Surat")
        self.svc.add_transaction(c.id, "UDHAAR", self.svc.today(), "Box", amount="500", notes="first")
        self.svc.add_transaction(c.id, "JAMA", self.svc.today(), "Pay", amount="200")
        self.ledger = self.svc.ledger(c.id)

    def html(self, *keys):
        return self.exports.statement_html(self.ledger, "Shop", "Details", items=frozenset(keys))

    def test_balance_is_off_by_default(self):
        self.assertFalse(pagesetup.load(self.svc).show_balance)
        self.assertNotIn("balance", self.exports.statement_html(
            self.ledger, items=frozenset(pagesetup.DEFAULT_ITEMS)).lower())

    def test_everything_when_items_is_none(self):
        html = self.exports.statement_html(self.ledger, "Shop", "Details")
        for text in ("Balance", "Closing balance", "Surat", "Printed on", "Details", "first"):
            self.assertIn(text, html)

    def test_only_chosen_columns_are_printed(self):
        html = self.html("date", "item", "debit")
        self.assertIn("<th style='padding:3px;'>Item / Description</th>", html)
        self.assertNotIn(">Qty<", html)
        self.assertNotIn(">Rate<", html)
        self.assertNotIn(">Notes<", html)
        self.assertNotIn("first", html)
        self.assertNotIn("Credit", html)
        self.assertEqual(html.count("<th "), 3)

    def test_blocks_can_be_left_out(self):
        html = self.html("date", "item", "debit", "credit")
        for text in ("Surat", "9876543210", "Printed on", "Details", "Transactions in period"):
            self.assertNotIn(text, html)
        html = self.html("date", "item", "debit", "credit", "shop", "contact", "summary", "printed_on")
        for text in ("Surat", "Printed on", "Details", "Transactions in period"):
            self.assertIn(text, html)

    def test_balance_adds_column_and_opening_closing(self):
        html = self.html("date", "item", "debit", "credit", "balance")
        self.assertIn("Opening balance", html)
        self.assertIn("Period totals / Closing balance", html)
        self.assertIn(">Balance<", html)

    def test_no_item_column_puts_labels_in_first_column(self):
        html = self.html("date", "debit", "balance")
        self.assertIn("Opening balance", html)

    def test_selection_is_remembered_in_order(self):
        pagesetup.save(self.svc, pagesetup.PageSetup(items=("notes", "date", "balance")))
        self.assertEqual(pagesetup.load(self.svc).items, ("date", "balance", "notes"))

    def test_at_least_one_column_is_required(self):
        with self.assertRaises(ValidationError):
            pagesetup.save(self.svc, pagesetup.PageSetup(items=("shop", "summary")))

    def test_previous_version_balance_setting_is_still_honoured(self):
        self.svc.set_setting("print_show_balance", "1")
        self.assertTrue(pagesetup.load(self.svc).show_balance)


if __name__ == "__main__":
    unittest.main()
