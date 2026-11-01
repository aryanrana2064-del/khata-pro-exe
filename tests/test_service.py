"""Edge cases: edit/delete recalculation, validation, duplicates, filters, SQL safety."""
import unittest

from tests.helpers import ServiceTestCase
from vx7khata import dates, money
from vx7khata.service import DuplicateEntryError, NotFoundError, ValidationError


class TestEditDelete(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")
        self.t1 = self.add(self.c.id, "UDHAAR", "10-01-2025", "A", "5000")
        self.t2 = self.add(self.c.id, "JAMA", "15-01-2025", "B", "2000")

    def test_edit_date_amount_type_item_notes_recalculates_and_keeps_created_at(self):
        before = self.svc.get_transaction(self.t2.id)
        self.clock.advance(3600)
        after = self.svc.update_transaction(self.t2.id, "UDHAAR", "09-01-2025", "B edited", amount="300", notes="n")
        self.assertEqual(after.created_at, before.created_at)  # audit timestamp untouched
        self.assertNotEqual(after.updated_at, before.updated_at)
        led = self.svc.ledger(self.c.id)
        self.assertEqual([r.txn.item for r in led.rows], ["B edited", "A"])  # re-sorted chronologically
        self.assertEqual([r.balance for r in led.rows], [30000, 530000])
        self.assertEqual(self.svc.customer_balance(self.c.id), 530000)

    def test_delete_recalculates(self):
        self.svc.delete_transaction(self.t1.id)
        led = self.svc.ledger(self.c.id)
        self.assertEqual(led.closing_balance, -200000)  # advance paid
        self.assertEqual(len(led.rows), 1)
        with self.assertRaises(NotFoundError):
            self.svc.get_transaction(self.t1.id)

    def test_same_date_entries_keep_entry_order(self):
        a = self.add(self.c.id, "UDHAAR", "20-01-2025", "first", "1")
        b = self.add(self.c.id, "UDHAAR", "20-01-2025", "second", "2")
        c = self.add(self.c.id, "UDHAAR", "20-01-2025", "third", "3")
        items = [r.txn.item for r in self.svc.ledger(self.c.id).rows if r.txn.txn_date == "2025-01-20"]
        self.assertEqual(items, ["first", "second", "third"])

    def test_update_to_qty_rate(self):
        t = self.svc.update_transaction(self.t1.id, "UDHAAR", "10-01-2025", "A", quantity="2.5", rate="12.50")
        self.assertEqual(t.amount_paise, 3125)
        self.assertEqual(t.quantity, "2.5")

    def test_opening_balance_uses_entries_before_range(self):
        self.add(self.c.id, "UDHAAR", "01-03-2025", "March", "100")
        led = self.svc.ledger(self.c.id, date_from="01-02-2025", date_to="28-02-2025")
        self.assertEqual(led.opening_balance, 300000)
        self.assertEqual(led.rows, [])
        self.assertEqual(led.closing_balance, 300000)
        led = self.svc.ledger(self.c.id, date_from="15-01-2025")
        self.assertEqual(led.opening_balance, 500000)
        self.assertEqual([r.balance for r in led.rows], [300000, 310000])
        self.assertEqual(led.closing_balance, 310000)

    def test_range_boundaries_are_inclusive(self):
        led = self.svc.ledger(self.c.id, date_from="10-01-2025", date_to="15-01-2025")
        self.assertEqual(len(led.rows), 2)
        self.assertEqual(led.opening_balance, 0)

    def test_bad_range(self):
        with self.assertRaises(ValidationError):
            self.svc.ledger(self.c.id, date_from="20-01-2025", date_to="10-01-2025")


class TestValidation(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")

    def bad(self, *args, **kw):
        with self.assertRaises(ValidationError):
            self.svc.add_transaction(self.c.id, *args, **kw)

    def test_rejects(self):
        self.bad("UDHAAR", "10-01-2025", "", amount="10")  # no item
        self.bad("BORROW", "10-01-2025", "x", amount="10")  # bad type
        self.bad("UDHAAR", "10-01-2025", "x", amount="0")
        self.bad("UDHAAR", "10-01-2025", "x", amount="-5")
        self.bad("UDHAAR", "10-01-2025", "x", amount="abc")
        self.bad("UDHAAR", "10-01-2025", "x", amount="10.005")
        self.bad("UDHAAR", "10-01-2025", "x")  # no amount
        self.bad("UDHAAR", "10-01-2025", "x", quantity="3")  # qty without rate or amount
        self.bad("UDHAAR", "31-02-2025", "x", amount="10")  # impossible date
        self.bad("UDHAAR", "2025-13-45x", "x", amount="10")
        self.bad("UDHAAR", "04-10-2026", "x", amount="10")  # tomorrow (today is 03-10-2026)
        self.bad("UDHAAR", "10-01-2025", "x", quantity="3", rate="200", amount="601")  # mismatch
        self.bad("UDHAAR", "10-01-2025", "x", quantity="0", rate="200")
        self.bad("UDHAAR", "10-01-1800", "x", amount="10")
        self.assertEqual(self.svc.ledger(self.c.id).rows, [])  # nothing half-saved

    def test_accepts_formats(self):
        self.add(self.c.id, "udhaar", "1-2-2025", "x", "1,500")
        self.add(self.c.id, "JAMA", "03/02/2025", "y", "₹250.50")
        self.add(self.c.id, "UDHAAR", "03.10.2026", "today ok", "1")
        self.assertEqual(self.svc.ledger(self.c.id).closing_balance, 150000 - 25050 + 100)

    def test_amount_matching_qty_rate_is_accepted(self):
        t = self.add(self.c.id, "UDHAAR", "10-01-2025", "x", amount="600.00", quantity="3", rate="200")
        self.assertEqual(t.amount_paise, 60000)

    def test_unknown_customer(self):
        with self.assertRaises(NotFoundError):
            self.svc.add_transaction(999, "UDHAAR", "10-01-2025", "x", amount="1")


class TestDuplicateGuard(ServiceTestCase):
    def test_rapid_identical_submit_blocked_then_allowed_later_or_when_forced(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="100")
        self.clock.advance(2)
        with self.assertRaises(DuplicateEntryError):
            self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "chain", amount="100")  # case-insensitive
        self.assertEqual(len(self.svc.ledger(c.id).rows), 1)
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="100", allow_duplicate=True)
        self.clock.advance(60)
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="100")  # genuine repeat later
        self.assertEqual(len(self.svc.ledger(c.id).rows), 3)

    def test_different_amount_is_not_duplicate(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="100")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="101")


class TestCustomers(ServiceTestCase):
    def test_crud_search_and_totals(self):
        r = self.svc.add_customer("  Rahul   Sharma ", "+91 98765-43210", "12 Main Road", "VIP")
        a = self.svc.add_customer("Amit", "")
        self.assertEqual(r.name, "Rahul Sharma")
        self.add(r.id, "UDHAAR", "10-01-2025", "x", "100")
        self.assertEqual([s.customer.name for s in self.svc.list_customers("rahul")], ["Rahul Sharma"])
        self.assertEqual([s.customer.name for s in self.svc.list_customers("98765 4")], ["Rahul Sharma"])
        self.assertEqual([s.customer.name for s in self.svc.list_customers("+91 (98765)")], ["Rahul Sharma"])
        self.assertEqual(len(self.svc.list_customers("")), 2)
        self.assertEqual(self.svc.list_customers("zzz"), [])
        u = self.svc.update_customer(r.id, "Rahul S", "9876543210", "New addr", "")
        self.assertEqual((u.name, u.address), ("Rahul S", "New addr"))
        self.assertEqual(self.svc.list_customers("rahul")[0].balance, 10000)

    def test_duplicate_names_rejected_case_insensitive(self):
        self.svc.add_customer("Rahul")
        with self.assertRaises(ValidationError):
            self.svc.add_customer("rahul")
        other = self.svc.add_customer("Amit")
        with self.assertRaises(ValidationError):
            self.svc.update_customer(other.id, "RAHUL")

    def test_validation(self):
        for bad in ({"name": ""}, {"name": "x" * 500}, {"name": "A", "mobile": "abc"}, {"name": "A", "mobile": "123"}):
            with self.assertRaises(ValidationError):
                self.svc.add_customer(**bad)

    def test_delete_only_without_entries(self):
        c = self.svc.add_customer("Rahul")
        e = self.svc.add_customer("Empty")
        self.add(c.id, "UDHAAR", "10-01-2025", "x", "1")
        with self.assertRaises(ValidationError):
            self.svc.delete_customer(c.id)
        self.svc.delete_customer(e.id)
        self.assertEqual(len(self.svc.list_customers()), 1)

    def test_unicode_names_and_items(self):
        c = self.svc.add_customer("राहुल शर्मा")
        self.add(c.id, "UDHAAR", "10-01-2025", "सोने की चेन", "100")
        self.assertEqual(len(self.svc.ledger(c.id, search="चेन").rows), 1)

    def test_sql_injection_text_is_just_text(self):
        evil = "Robert'); DROP TABLE customers;--"
        c = self.svc.add_customer(evil)
        self.add(c.id, "UDHAAR", "10-01-2025", evil, "5")
        self.assertEqual(self.svc.list_customers()[0].customer.name, evil)
        self.assertEqual(self.svc.ledger(c.id).rows[0].txn.item, evil)
        self.assertEqual(len(self.svc.list_customers(evil)), 1)


class TestDashboardAndRegister(ServiceTestCase):
    def test_dashboard_counts_today_and_historical(self):
        a = self.svc.add_customer("A")
        b = self.svc.add_customer("B")
        self.add(a.id, "UDHAAR", "03-10-2026", "today", "100")  # today in FakeClock
        self.add(a.id, "JAMA", "03-10-2026", "today2", "40")
        self.add(b.id, "UDHAAR", "10-01-2025", "old", "500")
        d = self.svc.dashboard()
        self.assertEqual((d.total_customers, d.total_transactions), (2, 3))
        self.assertEqual((d.todays_transactions, d.historical_transactions), (2, 1))
        self.assertEqual((d.total_udhaar, d.total_jama, d.net_outstanding), (60000, 4000, 56000))

    def test_empty_dashboard(self):
        d = self.svc.dashboard()
        self.assertEqual((d.total_customers, d.total_udhaar, d.todays_transactions, d.historical_transactions), (0, 0, 0, 0))

    def test_old_entry_reflected_immediately(self):
        c = self.svc.add_customer("A")
        self.add(c.id, "UDHAAR", "10-01-2025", "old", "5000")
        self.assertEqual(self.svc.dashboard().total_udhaar, 500000)
        self.assertEqual(self.svc.list_customers()[0].total_udhaar, 500000)

    def test_register_filters(self):
        a = self.svc.add_customer("A")
        b = self.svc.add_customer("B")
        self.add(a.id, "UDHAAR", "10-01-2025", "Gold ring", "1000")
        self.add(b.id, "UDHAAR", "11-01-2025", "Gold chain", "2000")
        self.add(b.id, "JAMA", "20-01-2025", "Payment", "500")
        full = self.svc.register()
        self.assertEqual((len(full.rows), full.total_udhaar, full.total_jama, full.net), (3, 300000, 50000, 250000))
        self.assertEqual(len(self.svc.register(customer_ids=[b.id]).rows), 2)
        self.assertEqual(len(self.svc.register(search="gold").rows), 2)
        self.assertEqual(len(self.svc.register(date_from="11-01-2025", date_to="15-01-2025").rows), 1)
        self.assertEqual(self.svc.register(customer_ids=[]).rows, [])


class TestMoneyAndDates(unittest.TestCase):
    def test_indian_grouping(self):
        f = money.format_inr
        self.assertEqual(f(0), "₹0.00")
        self.assertEqual(f(99), "₹0.99")
        self.assertEqual(f(150000), "₹1,500.00")
        self.assertEqual(f(12345678), "₹1,23,456.78")
        self.assertEqual(f(123456789012), "₹1,23,45,67,890.12")
        self.assertEqual(f(-50000), "-₹500.00")
        self.assertEqual(f(100000, symbol=False), "1,000.00")

    def test_no_float_drift(self):
        self.assertEqual(money.rupees_to_paise("0.1") + money.rupees_to_paise("0.2"), 30)
        self.assertEqual(money.rupees_to_paise("19.99"), 1999)
        q = money.parse_quantity("0.125")
        self.assertEqual(money.compute_amount_paise(q, money.rupees_to_paise("100")), 1250)
        self.assertEqual(money.compute_amount_paise(money.parse_quantity("3"), 20000), 60000)

    def test_dates(self):
        self.assertEqual(dates.parse_date("10-01-2025"), "2025-01-10")
        self.assertEqual(dates.format_date("2025-01-10"), "10-01-2025")
        for bad in ("", "29-02-2025", "10-13-2025", "hello", "10-01-25"):
            with self.assertRaises(dates.DateError):
                dates.parse_date(bad)
        self.assertEqual(dates.parse_date("29-02-2024"), "2024-02-29")  # leap year


if __name__ == "__main__":
    unittest.main()
