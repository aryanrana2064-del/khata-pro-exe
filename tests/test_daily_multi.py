"""Daily Entry: several products for one customer in one go, big scrolling notes box.

The pure helpers are tested with the real service; the page itself is exercised offscreen (needs PySide6).
    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_daily_multi -v
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestLineHelpers(unittest.TestCase):
    def test_line_values_with_quantity_and_rate(self):
        from vx7khata.ui.daily_entry import line_values
        v = line_values("UDHAAR", "08-10-2026", " Atta ", "5", "42.50", " note ")
        self.assertEqual((v["item"], v["quantity"], v["rate"], v["amount"], v["notes"]), ("Atta", "5", "42.50", None, "note"))

    def test_line_values_amount_only_and_payment_default(self):
        from vx7khata.labels import QUICK_PAYMENT_ITEM
        from vx7khata.ui.daily_entry import line_values
        v = line_values("JAMA", "08-10-2026", "", "", "500")
        self.assertEqual((v["item"], v["quantity"], v["rate"], v["amount"]), (QUICK_PAYMENT_ITEM, "", "", "500"))

    def test_line_is_empty(self):
        from vx7khata.ui.daily_entry import line_is_empty, row_total_paise
        self.assertTrue(line_is_empty("", " ", ""))
        self.assertFalse(line_is_empty("", "", "1"))
        self.assertEqual(row_total_paise("5", "42.50"), 21250)
        self.assertIsNone(row_total_paise("5", ""))


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestMultiProductPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from vx7khata.service import KhataService
        from vx7khata.ui.daily_entry import DailyEntryPage

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.svc = KhataService(os.path.join(self.tmp.name, "khata.db"))
        self.addCleanup(self.svc.close)
        self.customer = self.svc.add_customer("Rahul")
        self.messages = []
        self.page = DailyEntryPage(self.svc, on_saved=lambda: None,
                                   info=lambda t, m: self.messages.append(("info", m)),
                                   warn=lambda t, m: self.messages.append(("warn", m)),
                                   confirm=lambda *a, **k: True)
        self.addCleanup(self.page.deleteLater)
        self.page.set_customers(self.svc.list_customers())

    def _fill(self, block, rows, notes=""):
        block.customer.setCurrentIndex(block.customer.findData(self.customer.id))
        while len(block.lines) < len(rows):
            block.add_line()
        for line, (product, qty, rate) in zip(block.lines, rows):
            line.product.setText(product)
            line.qty.setText(qty)
            line.rate.setText(rate)
        block.notes.setPlainText(notes)

    def test_many_products_one_customer(self):
        block = self.page.blocks[0]
        self._fill(block, [("Atta", "5", "40"), ("Dal", "2", "110"), ("Oil", "1", "150.50")], notes="deliver tomorrow")
        self.assertEqual(block.total_paise(), 20000 + 22000 + 15050)
        self.page.save_all()
        pending = self.svc.list_pending()
        self.assertEqual(sorted(p.item for p in pending), ["Atta", "Dal", "Oil"])
        self.assertEqual(sum(p.amount_paise for p in pending), 57050)
        # the note is stored once (with the first product), not repeated on every line
        self.assertEqual(sorted(p.notes for p in pending), ["", "", "deliver tomorrow"])
        self.assertEqual(self.messages[-1][0], "info")
        # everything was added -> the page is back to a fresh empty block
        self.assertEqual(len(self.page.blocks), 1)
        self.assertTrue(self.page.blocks[0].is_empty())

    def test_two_customers_in_one_go(self):
        other = self.svc.add_customer("Sita")
        self.page.set_customers(self.svc.list_customers())
        first = self.page.blocks[0]
        self._fill(first, [("Atta", "1", "40")])
        second = self.page.add_block()
        self._fill(second, [("Dal", "2", "100"), ("Rice", "3", "50")])
        second.customer.setCurrentIndex(second.customer.findData(other.id))
        self.page.save_all()
        by_name = {}
        for p in self.svc.list_pending():
            by_name.setdefault(p.customer_name, []).append(p.item)
        self.assertEqual({k: sorted(v) for k, v in by_name.items()}, {"Rahul": ["Atta"], "Sita": ["Dal", "Rice"]})

    def test_bad_line_stops_and_keeps_the_rest(self):
        block = self.page.blocks[0]
        self._fill(block, [("Atta", "5", "40"), ("Dal", "2", ""), ("Oil", "1", "150")])
        self.page.save_all()
        self.assertEqual([p.item for p in self.svc.list_pending()], ["Atta"])
        self.assertEqual(self.messages[-1][0], "warn")
        remaining = [ln.product.text() for ln in block.filled_lines()]
        self.assertEqual(remaining, ["Dal", "Oil"])      # fix 'Dal' and press again

    def test_notes_box_compact_and_grows_with_text(self):
        block = self.page.blocks[0]
        self.assertEqual(block.notes.minimumHeight(), 40)     # starts compact so more entries fit on screen
        block.notes.setPlainText("line\n" * 500)
        self.assertLessEqual(len(block.notes.toPlainText()), 2000)
        self.assertEqual(block.notes.maximumHeight(), 200)    # grows with the text, capped, then scrolls

    def test_remove_product_keeps_one_line(self):
        block = self.page.blocks[0]
        while len(block.lines) > 1:
            block.remove_line(block.lines[-1])
        block.lines[0].product.setText("x")
        block.remove_line(block.lines[0])
        self.assertEqual(len(block.lines), 1)
        self.assertEqual(block.lines[0].product.text(), "")

    def test_draft_saved_and_restored(self):
        from pathlib import Path
        from vx7khata.ui.daily_entry import DRAFT_FILE, DailyEntryPage
        block = self.page.blocks[0]
        self._fill(block, [("Atta", "5", "40")], notes="restore me")
        draft = Path(self.tmp.name) / DRAFT_FILE
        self.assertTrue(draft.is_file())
        # a "new start" of the page finds the draft and brings everything back
        page2 = DailyEntryPage(self.svc, on_saved=lambda: None,
                               info=lambda t, m: None, warn=lambda t, m: None,
                               confirm=lambda *a, **k: True)
        self.addCleanup(page2.deleteLater)
        page2.set_customers(self.svc.list_customers())
        self.assertTrue(page2.has_draft())
        self.assertEqual(page2.load_draft(), 1)
        b = page2.blocks[0]
        self.assertEqual(b.customer.currentText(), "Rahul")
        self.assertEqual((b.lines[0].product.text(), b.lines[0].qty.text(), b.lines[0].rate.text()),
                         ("Atta", "5", "40"))
        self.assertEqual(b.notes.toPlainText(), "restore me")

    def test_draft_cleared_after_everything_is_saved(self):
        from pathlib import Path
        from vx7khata.ui.daily_entry import DRAFT_FILE
        block = self.page.blocks[0]
        self._fill(block, [("Atta", "5", "40")])
        draft = Path(self.tmp.name) / DRAFT_FILE
        self.assertTrue(draft.is_file())
        self.page.save_all()
        self.assertFalse(draft.exists())


if __name__ == "__main__":
    unittest.main()
