import unittest
from datetime import date

from tests.helpers import ServiceTestCase
from vx7khata import viewmodel as vm
from vx7khata.service import AuditEntry


class TestFormatting(unittest.TestCase):
    def test_compact_inr(self):
        c = vm.compact_inr
        self.assertEqual(c(0), "0")
        self.assertEqual(c(49900), "499")
        self.assertEqual(c(100000), "1K")
        self.assertEqual(c(250000), "2.5K")
        self.assertEqual(c(10000000), "1L")
        self.assertEqual(c(15000000), "1.5L")
        self.assertEqual(c(1000000000), "1Cr")
        self.assertEqual(c(-250000), "-2.5K")

    def test_nice_axis(self):
        self.assertEqual(vm.nice_axis(0), [0, 100000, 200000, 300000, 400000])  # default step Rs 1,000
        for peak in (1, 99, 100000, 123456, 987654321, 5000000000):
            axis = vm.nice_axis(peak)
            self.assertEqual(len(axis), 5)
            self.assertEqual(axis[0], 0)
            self.assertGreaterEqual(axis[-1], peak, peak)
            self.assertTrue(all(b > a for a, b in zip(axis, axis[1:])))
            steps = {b - a for a, b in zip(axis, axis[1:])}
            self.assertEqual(len(steps), 1)  # evenly spaced
        self.assertEqual(vm.nice_axis(40000000)[-1], 40000000)  # exactly 4,00,000 -> no wasted headroom
        self.assertEqual(vm.nice_axis(40000001)[-1], 80000000)  # just above 4,00,000: step jumps to 2,00,000 x 4
        self.assertEqual(vm.nice_axis(1), [0, 100, 200, 300, 400])  # sub-rupee peaks still give distinct ticks
        with self.assertRaises(ValueError):
            vm.nice_axis(10, ticks=0)

    def test_month_label(self):
        self.assertEqual(vm.month_label("2026-10"), "Oct 26")
        self.assertEqual(vm.month_label("2025-01"), "Jan 25")

    def test_due_text(self):
        today = date(2026, 10, 3)
        self.assertEqual(vm.due_text(None, today), "")
        self.assertEqual(vm.due_text("2026-10-03", today), "Due today")
        self.assertEqual(vm.due_text("2026-10-04", today), "Due in 1 day")
        self.assertEqual(vm.due_text("2026-10-13", today), "Due in 10 days")
        self.assertEqual(vm.due_text("2026-10-02", today), "Overdue 1 day")
        self.assertEqual(vm.due_text("2026-09-23", today), "Overdue 10 days")

    def test_action_and_timestamp(self):
        self.assertEqual(vm.action_label("ENTRY_ADD"), "Entry added")
        self.assertEqual(vm.action_label("SOMETHING_NEW"), "Something New")
        self.assertEqual(vm.timestamp_text("2026-10-03T13:05:09+05:30"), "03-10-2026 13:05")
        self.assertEqual(vm.timestamp_text("garbage"), "garbage")


class TestCreditState(ServiceTestCase):
    def _summary(self, name, limit, udhaar):
        c = self.svc.add_customer(name, credit_limit=limit)
        if udhaar:
            self.add(c.id, "UDHAAR", "01-10-2026", "x", str(udhaar), allow_over_limit=True)
        return next(x for x in self.svc.list_customers() if x.customer.id == c.id)

    def test_no_limit(self):
        self.assertEqual(vm.credit_state(self._summary("A", "", 100)), ("none", 0.0))
        self.assertEqual(vm.credit_text(self._summary("B", "", 0)), "No limit")

    def test_ok(self):
        s = self._summary("C", "1000", 500)
        self.assertEqual(vm.credit_state(s), ("ok", 0.5))
        self.assertEqual(vm.credit_text(s), "\u20b91,000.00  (50% used)")

    def test_warn_starts_at_80_percent(self):
        self.assertEqual(vm.credit_state(self._summary("D", "1000", 799))[0], "ok")
        self.assertEqual(vm.credit_state(self._summary("E", "1000", 800))[0], "warn")

    def test_exactly_at_limit_is_warn_not_over(self):
        self.assertEqual(vm.credit_state(self._summary("F", "1000", 1000))[0], "warn")

    def test_over(self):
        state, fraction = vm.credit_state(self._summary("G", "1000", 1001))
        self.assertEqual(state, "over")
        self.assertGreater(fraction, 1.0)

    def test_advance_payment_counts_as_zero_use(self):
        c = self.svc.add_customer("H", credit_limit="1000")
        self.add(c.id, "JAMA", "01-10-2026", "advance", "500")
        s = next(x for x in self.svc.list_customers() if x.customer.id == c.id)
        self.assertEqual(vm.credit_state(s), ("ok", 0.0))


class TestAuditText(unittest.TestCase):
    def _entry(self, details):
        return AuditEntry(1, "2026-10-03T13:00:00+05:30", "ENTRY_EDIT", "transaction", 1, 1, "s", details)

    def test_lines(self):
        e = self._entry('{"amount_paise": [50000, 60000], "txn_date": ["2025-01-10", "2025-01-11"], "notes": ["", "n"]}')
        self.assertEqual(vm.audit_details_lines(e), [
            "Amount: ₹500.00  →  ₹600.00",
            "Notes: (empty)  →  n",
            "Date: 10-01-2025  →  11-01-2025",
        ])

    def test_single_values_and_edge_inputs(self):
        e = self._entry('{"over_credit_limit": true, "credit_limit_paise": 0}')
        self.assertEqual(vm.audit_details_lines(e), ["Credit limit: none", "Saved above credit limit: True"])
        self.assertEqual(vm.audit_details_lines(self._entry("")), [])
        self.assertEqual(vm.audit_details_lines(self._entry("not json")), ["not json"])
        self.assertEqual(vm.audit_details_lines(self._entry("[1, 2]")), ["[1, 2]"])
        deleted = self._entry('{"created_at": "2026-10-03T13:00:00+05:30", "amount_paise": 100}')
        self.assertEqual(vm.audit_details_lines(deleted),
                         ["Amount: ₹1.00", "Originally entered: 03-10-2026 13:00"])


if __name__ == "__main__":
    unittest.main()
