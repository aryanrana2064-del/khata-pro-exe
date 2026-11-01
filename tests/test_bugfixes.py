"""Regression tests for the bugs found in the v3 review (no Qt needed)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from vx7khata import db as dbmod, money, viewmodel
from vx7khata.service import KhataService, NotFoundError, ValidationError


def make_service(clock=None):
    return KhataService(os.path.join(tempfile.mkdtemp(), "k.db"), clock=clock)


class CustomerSearchTests(unittest.TestCase):
    def test_digits_in_a_name_do_not_match_every_phone(self):
        svc = make_service()
        svc.add_customer("Shop 2", mobile="9876543210")
        svc.add_customer("Ramesh", mobile="9811223344")
        names = [s.customer.name for s in svc.list_customers("Shop 2")]
        self.assertEqual(names, ["Shop 2"])

    def test_phone_search_still_works(self):
        svc = make_service()
        svc.add_customer("Ramesh", mobile="98112 23344")
        svc.add_customer("Suresh", mobile="9999999999")
        self.assertEqual([s.customer.name for s in svc.list_customers("1122")], ["Ramesh"])
        self.assertEqual([s.customer.name for s in svc.list_customers("(98112) 23")], ["Ramesh"])


class PendingRaceTests(unittest.TestCase):
    def setUp(self):
        self.now = [datetime(2026, 10, 8, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))]
        self.svc = make_service(lambda: self.now[0])
        self.cust = self.svc.add_customer("A")

    def test_a_waiting_row_cannot_be_saved_twice(self):
        p = self.svc.add_pending_entry(self.cust.id, "UDHAAR", "08-10-2026", "Box", amount="50")
        with dbmod.transaction(self.svc.conn):
            self.svc._post_pending_locked(p, "first window")
        with self.assertRaises(NotFoundError):
            with dbmod.transaction(self.svc.conn):
                self.svc._post_pending_locked(p, "second window, stale copy")
        self.assertEqual(self.svc.customer_balance(self.cust.id), 5000)

    def test_post_pending_twice_is_rejected_cleanly(self):
        p = self.svc.add_pending_entry(self.cust.id, "UDHAAR", "08-10-2026", "Box", amount="50")
        self.svc.post_pending(p.id)
        with self.assertRaises(NotFoundError):
            self.svc.post_pending(p.id)
        self.assertEqual(self.svc.customer_balance(self.cust.id), 5000)

    def test_due_rows_are_posted_once(self):
        self.svc.add_pending_entry(self.cust.id, "UDHAAR", "08-10-2026", "Box", amount="50")
        self.now[0] += timedelta(hours=24, seconds=1)
        self.assertEqual(len(self.svc.post_due_pending()), 1)
        self.assertEqual(len(self.svc.post_due_pending()), 0)
        self.assertEqual(self.svc.customer_balance(self.cust.id), 5000)


class TimestampTests(unittest.TestCase):
    def test_naive_stored_timestamp_does_not_crash_duplicate_check(self):
        svc = make_service()
        c = svc.add_customer("A")
        svc.add_transaction(c.id, "UDHAAR", svc.today(), "Box", amount="10")
        svc.conn.execute("UPDATE transactions SET created_at = '2026-01-01T10:00:00'")  # no UTC offset
        svc.add_transaction(c.id, "UDHAAR", svc.today(), "Box", amount="10")  # must not raise TypeError

    def test_naive_pending_timestamps(self):
        svc = make_service()
        c = svc.add_customer("A")
        p = svc.add_pending_entry(c.id, "UDHAAR", svc.today(), "Box", amount="10")
        svc.conn.execute("UPDATE pending_entries SET post_at = '2026-01-01T10:00:00'")
        self.assertEqual(svc.seconds_until_post(svc.get_pending_entry(p.id)), 0)
        self.assertEqual(len(svc.post_due_pending()), 1)


class MoneyCommaTests(unittest.TestCase):
    def test_proper_grouping_is_accepted(self):
        for text, paise in (("1,234", 123400), ("12,34,567", 123456700), ("1,234,567.50", 123456750),
                            ("₹ 5,000", 500000), ("500", 50000), ("12,345", 1234500)):
            self.assertEqual(money.rupees_to_paise(text), paise, text)

    def test_typos_are_rejected(self):
        for text in ("1,2,3", "12,34", ",500", "1,,000", "5,00", "1,23"):
            with self.assertRaises(money.MoneyError, msg=text):
                money.rupees_to_paise(text)


class CompactInrTests(unittest.TestCase):
    def test_values_move_up_a_unit_instead_of_printing_100k(self):
        self.assertEqual(viewmodel.compact_inr(99_960 * 100), "1L")
        self.assertEqual(viewmodel.compact_inr(9_999_000 * 100 // 100 * 100), "1Cr")

    def test_normal_values(self):
        for paise, text in ((250_000, "2.5K"), (15_000_000, "1.5L"), (300_000_000_0, "3Cr"), (500, "5"),
                            (-250_000, "-2.5K")):
            self.assertEqual(viewmodel.compact_inr(paise), text)


if __name__ == "__main__":
    unittest.main()
