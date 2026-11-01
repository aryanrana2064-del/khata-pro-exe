"""Regression tests for the fixes made after the v8 bug check."""
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ServiceTestCase
from vx7khata import exports, insights


class AsOfReportsTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.clock.now = self.clock.now.replace(month=10, day=9)  # "today" = 09-10-2026
        self.c = self.svc.add_customer("Ram")
        self.add(self.c.id, "UDHAAR", "01-08-2026", "old", amount="1000")
        self.add(self.c.id, "UDHAAR", "05-10-2026", "recent", amount="500", due_date="06-10-2026")
        self.add(self.c.id, "JAMA", "07-10-2026", "later payment", amount="1000")

    def test_aging_ignores_entries_after_the_date(self):
        rep = insights.aging_report(self.svc, as_of="01-09-2026")
        self.assertEqual(rep.total_outstanding, 100000)  # only the Rs 1000 of 01-08; the Oct entries do not exist yet
        self.assertEqual(rep.total_advance, 0)

    def test_aging_today_unchanged(self):
        rep = insights.aging_report(self.svc)
        self.assertEqual(rep.total_outstanding, 50000)  # payment of 07-10 settled the oldest udhaar first

    def test_overdue_not_reduced_by_a_later_payment(self):
        rows = insights.overdue_items(self.svc, as_of="08-10-2026")
        self.assertEqual([(r.txn.item, r.remaining) for r in rows], [("recent", 50000)])
        self.assertEqual(insights.overdue_items(self.svc, as_of="06-10-2026"), [])  # due 06-10 is not yet overdue

    def test_date_before_any_entry_gives_empty_report(self):
        self.assertEqual(insights.aging_report(self.svc, as_of="01-01-2026").rows, [])


class FallbackFontTests(unittest.TestCase):
    def setUp(self):
        exports._fonts()
        self._saved = (list(exports._FALLBACKS), set(exports._PRIMARY_GLYPHS))

    def tearDown(self):
        exports._FALLBACKS[:] = self._saved[0]
        exports._PRIMARY_GLYPHS.clear()
        exports._PRIMARY_GLYPHS.update(self._saved[1])

    def test_plain_text_is_only_escaped(self):
        self.assertEqual(exports._esc("Ram & <Sons> ₹5"), "Ram &amp; &lt;Sons&gt; ₹5")

    def test_unsupported_letters_switch_to_fallback_font(self):
        exports._PRIMARY_GLYPHS.difference_update({ord("é"), ord("ü")})
        exports._FALLBACKS[:] = [("VX7Sans", "VX7Sans-Bold", {ord("é"), ord("ü"), 0x200D})]
        self.assertEqual(exports._esc("Café 5"), 'Caf<font name="VX7Sans">é</font> 5')
        self.assertEqual(exports._esc("é\u200dü", bold=True),
                         '<font name="VX7Sans-Bold">é\u200dü</font>')

    def test_pdf_still_builds_with_fallback_in_use(self):
        from vx7khata.service import Customer, Ledger
        exports._PRIMARY_GLYPHS.discard(ord("é"))
        exports._FALLBACKS[:] = [("VX7Sans", "VX7Sans-Bold", {ord("é")})]
        cust = Customer(1, "René", "", "", "", "2026-10-03", "2026-10-03")
        led = Ledger(cust, None, None, "", 0, [], 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as d:
            out = exports.export_statement_pdf(led, Path(d) / "s.pdf", shop_name="Café")
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
