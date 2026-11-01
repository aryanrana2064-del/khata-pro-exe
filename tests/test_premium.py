"""Premium features: credit limits, due dates, audit log, aging, overdue, trends, reminders, PIN, auto backup."""
import json
import random
import sqlite3
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

from tests.helpers import ServiceTestCase
from vx7khata import backup, db, insights, security
from vx7khata.service import (
    CreditLimitError, KhataService, NotFoundError, ValidationError,
)


# --------------------------------------------------------------------------- credit limit
class TestCreditLimit(ServiceTestCase):
    def test_limit_blocks_udhaar_unless_overridden(self):
        c = self.svc.add_customer("Rahul", credit_limit="5,000")
        self.assertEqual(c.credit_limit_paise, 500000)
        self.add(c.id, "UDHAAR", "01-10-2026", "A", "3000")
        with self.assertRaises(CreditLimitError) as ctx:
            self.add(c.id, "UDHAAR", "02-10-2026", "B", "2500")
        self.assertEqual(ctx.exception.new_balance_paise, 550000)
        self.assertEqual(ctx.exception.limit_paise, 500000)
        self.assertEqual(self.svc.customer_balance(c.id), 300000)  # nothing saved
        t = self.add(c.id, "UDHAAR", "02-10-2026", "B", "2500", allow_over_limit=True)
        self.assertEqual(self.svc.customer_balance(c.id), 550000)
        details = json.loads(self.svc.audit_entries(1)[0].details)
        self.assertTrue(details["over_credit_limit"])
        self.assertEqual(self.svc.audit_entries(1)[0].entity_id, t.id)

    def test_exactly_at_limit_is_allowed_and_jama_never_blocked(self):
        c = self.svc.add_customer("Rahul", credit_limit="1000")
        self.add(c.id, "UDHAAR", "01-10-2026", "A", "1000")
        self.add(c.id, "JAMA", "02-10-2026", "pay", "5000")
        self.assertEqual(self.svc.customer_balance(c.id), -400000)

    def test_update_limit(self):
        c = self.svc.add_customer("Rahul")
        self.assertEqual(c.credit_limit_paise, 0)
        c = self.svc.update_customer(c.id, "Rahul", credit_limit="2000")
        self.assertEqual(c.credit_limit_paise, 200000)
        c = self.svc.update_customer(c.id, "Rahul Sharma")  # None keeps the limit
        self.assertEqual(c.credit_limit_paise, 200000)
        c = self.svc.update_customer(c.id, "Rahul Sharma", credit_limit="")  # blank removes it
        self.assertEqual(c.credit_limit_paise, 0)
        for bad in ("abc", "-5", "10.555"):
            with self.assertRaises(ValidationError):
                self.svc.update_customer(c.id, "Rahul Sharma", credit_limit=bad)

    def test_over_limit_report_and_usage(self):
        a = self.svc.add_customer("A", credit_limit="1000")
        b = self.svc.add_customer("B", credit_limit="1000")
        self.svc.add_customer("C")  # no limit
        self.add(a.id, "UDHAAR", "01-10-2026", "x", "1500", allow_over_limit=True)
        self.add(b.id, "UDHAAR", "01-10-2026", "x", "250")
        over = insights.over_limit_customers(self.svc)
        self.assertEqual([s.customer.name for s in over], ["A"])
        usage = {s.customer.name: insights.credit_usage(s) for s in self.svc.list_customers()}
        self.assertAlmostEqual(usage["A"], 1.5)
        self.assertAlmostEqual(usage["B"], 0.25)
        self.assertIsNone(usage["C"])


# --------------------------------------------------------------------------- due dates
class TestDueDates(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")

    def test_due_date_saved_and_editable(self):
        t = self.add(self.c.id, "UDHAAR", "10-01-2025", "Chain", "1000", due_date="10-02-2025")
        self.assertEqual(t.due_date, "2025-02-10")
        t = self.svc.update_transaction(t.id, "UDHAAR", "10-01-2025", "Chain", amount="1000", due_date="")
        self.assertIsNone(t.due_date)

    def test_future_due_date_is_fine_but_before_txn_date_is_not(self):
        self.add(self.c.id, "UDHAAR", "01-10-2026", "Chain", "100", due_date="01-01-2027")
        with self.assertRaises(ValidationError):
            self.add(self.c.id, "UDHAAR", "10-01-2025", "x", "100", due_date="09-01-2025")
        with self.assertRaises(ValidationError):
            self.add(self.c.id, "UDHAAR", "10-01-2025", "x", "101", due_date="not a date")

    def test_jama_cannot_have_due_date(self):
        with self.assertRaises(ValidationError):
            self.add(self.c.id, "JAMA", "10-01-2025", "pay", "100", due_date="10-02-2025")
        t = self.add(self.c.id, "UDHAAR", "10-01-2025", "x", "100", due_date="10-02-2025")
        with self.assertRaises(ValidationError):  # switching to JAMA while keeping a due date
            self.svc.update_transaction(t.id, "JAMA", "10-01-2025", "x", amount="100", due_date="10-02-2025")


# --------------------------------------------------------------------------- audit log
class TestAuditLog(ServiceTestCase):
    def test_full_trail(self):
        c = self.svc.add_customer("Rahul", credit_limit="900")
        self.svc.update_customer(c.id, "Rahul S", "9876543210")
        t = self.add(c.id, "UDHAAR", "10-01-2025", "Chain", "500")
        self.svc.update_transaction(t.id, "UDHAAR", "11-01-2025", "Chain", amount="600", notes="n")
        self.svc.delete_transaction(t.id)
        e = self.svc.add_customer("Empty")
        self.svc.delete_customer(e.id)

        log = self.svc.audit_entries()
        self.assertEqual([x.action for x in log], [
            "CUSTOMER_DELETE", "CUSTOMER_ADD", "ENTRY_DELETE", "ENTRY_EDIT", "ENTRY_ADD", "CUSTOMER_EDIT", "CUSTOMER_ADD"])
        edit = next(x for x in log if x.action == "ENTRY_EDIT")
        changes = json.loads(edit.details)
        self.assertEqual(changes["amount_paise"], [50000, 60000])
        self.assertEqual(changes["txn_date"], ["2025-01-10", "2025-01-11"])
        self.assertEqual(changes["notes"], ["", "n"])
        self.assertNotIn("item", changes)  # unchanged fields are not listed
        deleted = json.loads(next(x for x in log if x.action == "ENTRY_DELETE").details)
        self.assertEqual((deleted["amount_paise"], deleted["item"]), (60000, "Chain"))
        cust_edit = json.loads(next(x for x in log if x.action == "CUSTOMER_EDIT").details)
        self.assertEqual(cust_edit["name"], ["Rahul", "Rahul S"])
        self.assertIn("₹500.00", next(x for x in log if x.action == "ENTRY_ADD").summary)

    def test_log_survives_deleting_the_entry_and_filters(self):
        a = self.svc.add_customer("A")
        b = self.svc.add_customer("B")
        t = self.add(a.id, "UDHAAR", "10-01-2025", "Gold ring", "500")
        self.add(b.id, "UDHAAR", "10-01-2025", "Silver", "500")
        self.svc.delete_transaction(t.id)
        self.assertEqual(self.svc.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.assertEqual(len(self.svc.audit_entries(customer_id=a.id)), 3)  # add customer, add, delete
        self.assertEqual(len(self.svc.audit_entries(search="gold ring")), 2)
        self.assertEqual(len(self.svc.audit_entries(search="100%")), 0)  # % is literal, not a wildcard
        self.assertEqual(len(self.svc.audit_entries(limit=2)), 2)

    def test_no_audit_row_for_failed_or_unchanged_operations(self):
        c = self.svc.add_customer("Rahul")
        before = len(self.svc.audit_entries())
        with self.assertRaises(ValidationError):
            self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "", amount="1")
        self.svc.update_customer(c.id, "Rahul")  # nothing changed
        self.assertEqual(len(self.svc.audit_entries()), before)
        t = self.add(c.id, "UDHAAR", "10-01-2025", "Chain", "500", notes="n")
        before = len(self.svc.audit_entries())
        self.svc.update_transaction(t.id, "UDHAAR", "10-01-2025", "Chain", amount="500", notes="n")  # identical
        self.assertEqual(len(self.svc.audit_entries()), before)
        self.svc.update_transaction(t.id, "UDHAAR", "10-01-2025", "Chain", amount="501", notes="n")
        self.assertEqual(len(self.svc.audit_entries()), before + 1)

    def test_restore_is_logged(self):
        self.svc.add_customer("Rahul")
        bk = Path(self.tmp.name) / "b.db"
        self.svc.create_backup(bk)
        self.svc.restore_backup(bk, safety_dir=Path(self.tmp.name) / "s")
        self.assertEqual(self.svc.audit_entries(1)[0].action, "RESTORE")


# --------------------------------------------------------------------------- migration
class TestMigrationToV3(ServiceTestCase):
    def _make_v2(self, path):
        conn = sqlite3.connect(path, isolation_level=None)
        for v in (1, 2):
            for stmt in db.MIGRATIONS[v]:
                conn.execute(stmt)
        conn.execute("PRAGMA user_version = 2")
        conn.execute("INSERT INTO customers(name, name_key, created_at, updated_at) VALUES('Old', 'old', 'n', 'n')")
        conn.execute("INSERT INTO transactions(customer_id, txn_date, item, amount_paise, txn_type, created_at, updated_at) "
                     "VALUES (1, '2025-01-10', 'kept', 12300, 'UDHAAR', 'n', 'n')")
        conn.close()

    def test_v2_data_is_kept_and_new_columns_default_sensibly(self):
        path = Path(self.tmp.name) / "v2.db"
        self._make_v2(path)
        svc = KhataService(path)
        self.addCleanup(svc.close)
        self.assertEqual(db.get_user_version(svc.conn), db.SCHEMA_VERSION)
        c = svc.get_customer(1)
        self.assertEqual(c.credit_limit_paise, 0)
        t = svc.get_transaction(1)
        self.assertEqual((t.item, t.amount_paise, t.due_date), ("kept", 12300, None))
        self.assertTrue((Path(self.tmp.name) / "v2.db.pre-v2-migration.bak").exists())
        self.assertEqual(svc.audit_entries(), [])

    def test_restoring_a_v2_backup_works(self):
        path = Path(self.tmp.name) / "v2.db"
        self._make_v2(path)
        self.assertEqual(backup.validate_backup(path).schema_version, 2)
        self.svc.restore_backup(path, safety_dir=Path(self.tmp.name) / "s")
        self.assertEqual(db.get_user_version(self.svc.conn), db.SCHEMA_VERSION)
        self.assertEqual(self.svc.ledger(1).rows[0].txn.item, "kept")

    def test_v3_backup_missing_new_columns_is_rejected(self):
        path = Path(self.tmp.name) / "fake3.db"
        conn = sqlite3.connect(path, isolation_level=None)
        for v in (1, 2):
            for stmt in db.MIGRATIONS[v]:
                conn.execute(stmt)
        conn.execute("PRAGMA user_version = 3")  # claims v3 but has no v3 columns/tables
        conn.close()
        with self.assertRaises(backup.BackupError):
            backup.validate_backup(path)


# --------------------------------------------------------------------------- aging / overdue
class TestAging(ServiceTestCase):
    def test_fifo_buckets(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "UDHAAR", "01-11-2024", "C", "200")   # 111 days old on 20-02-2025
        self.add(c.id, "UDHAAR", "10-01-2025", "A", "1000")  # 41 days
        self.add(c.id, "UDHAAR", "01-02-2025", "B", "500")   # 19 days
        self.add(c.id, "JAMA", "05-02-2025", "pay", "300")   # clears C (200) and 100 of A
        rep = insights.aging_report(self.svc, as_of="20-02-2025")
        self.assertEqual(rep.as_of, "2025-02-20")
        row = rep.rows[0]
        self.assertEqual(row.buckets, (50000, 90000, 0, 0))
        self.assertEqual(row.total, 140000)
        self.assertEqual(row.balance, self.svc.customer_balance(c.id))
        self.assertEqual(row.oldest_date, "2025-01-10")
        self.assertEqual(rep.bucket_totals, (50000, 90000, 0, 0))

    def test_bucket_edges(self):
        c = self.svc.add_customer("Rahul")
        for day, amount in (("03-09-2026", "1"), ("04-09-2026", "2"), ("04-08-2026", "4"), ("05-08-2026", "8")):
            self.add(c.id, "UDHAAR", day, day, amount)
        # as_of = 03-10-2026: ages are 30, 29, 60, 59 -> all within first two buckets
        row = insights.aging_report(self.svc).rows[0]
        self.assertEqual(row.buckets, (100 * (1 + 2), 100 * (4 + 8), 0, 0))
        c2 = self.svc.add_customer("Two")
        self.add(c2.id, "UDHAAR", "03-08-2026", "x", "1")   # 61 days -> 61-90
        self.add(c2.id, "UDHAAR", "04-07-2026", "y", "2")   # 91 days -> 90+
        self.add(c2.id, "UDHAAR", "05-07-2026", "z", "4")   # 90 days -> 61-90
        row2 = next(r for r in insights.aging_report(self.svc).rows if r.customer.name == "Two")
        self.assertEqual(row2.buckets, (0, 0, 500, 200))

    def test_advance_payment_offsets_later_udhaar(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "JAMA", "01-01-2025", "advance", "1000")
        rep = insights.aging_report(self.svc, as_of="01-02-2025")
        self.assertEqual((rep.rows[0].total, rep.rows[0].advance, rep.rows[0].balance), (0, 100000, -100000))
        self.add(c.id, "UDHAAR", "10-01-2025", "goods", "400")
        row = insights.aging_report(self.svc, as_of="01-02-2025").rows[0]
        self.assertEqual((row.total, row.advance), (0, 60000))

    def test_fully_settled_customers_are_left_out(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "UDHAAR", "10-01-2025", "x", "500")
        self.add(c.id, "JAMA", "11-01-2025", "pay", "500")
        self.svc.add_customer("Never traded")
        self.assertEqual(insights.aging_report(self.svc).rows, [])

    def test_conservation_on_random_data(self):
        rng = random.Random(7)
        customers = [self.svc.add_customer(f"C{i}") for i in range(6)]
        for n in range(250):
            c = rng.choice(customers)
            kind = rng.choice(["UDHAAR", "UDHAAR", "JAMA"])
            day = rng.randint(1, 28)
            month = rng.randint(1, 12)
            self.add(c.id, kind, f"{day:02d}-{month:02d}-2025", f"item{n}", str(rng.randint(1, 5000)))
        rep = insights.aging_report(self.svc, as_of="31-12-2025")
        by_id = {r.customer.id: r for r in rep.rows}
        for c in customers:
            balance = self.svc.customer_balance(c.id)
            row = by_id.get(c.id)
            if row is None:
                self.assertEqual(balance, 0)
            else:
                self.assertEqual(row.total - row.advance, balance, c.name)
                self.assertEqual(sum(row.buckets), row.total)
                self.assertTrue(row.total == 0 or row.advance == 0)  # never both open udhaar and advance credit

    def test_customer_filter(self):
        a = self.svc.add_customer("A")
        b = self.svc.add_customer("B")
        self.add(a.id, "UDHAAR", "10-01-2025", "x", "5")
        self.add(b.id, "UDHAAR", "10-01-2025", "x", "5")
        self.assertEqual([r.customer.name for r in insights.aging_report(self.svc, customer_ids=[b.id]).rows], ["B"])


class TestOverdue(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")

    def test_partial_payment_leaves_remaining_overdue(self):
        self.add(self.c.id, "UDHAAR", "10-01-2025", "Chain", "1000", due_date="10-02-2025")
        self.add(self.c.id, "JAMA", "15-01-2025", "part", "400")
        rows = insights.overdue_items(self.svc, as_of="20-02-2025")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].remaining, rows[0].days_overdue), (60000, 10))

    def test_paid_or_not_yet_due_or_no_due_date_are_not_overdue(self):
        self.add(self.c.id, "UDHAAR", "10-01-2025", "paid", "100", due_date="10-02-2025")
        self.add(self.c.id, "JAMA", "11-01-2025", "pay", "100")
        self.add(self.c.id, "UDHAAR", "12-01-2025", "future", "100", due_date="01-03-2025")
        self.add(self.c.id, "UDHAAR", "13-01-2025", "no due date", "100")
        self.assertEqual(insights.overdue_items(self.svc, as_of="20-02-2025"), [])

    def test_due_on_the_as_of_day_is_not_overdue_yet_but_is_due_soon(self):
        self.add(self.c.id, "UDHAAR", "10-01-2025", "x", "100", due_date="20-02-2025")
        self.assertEqual(insights.overdue_items(self.svc, as_of="20-02-2025"), [])
        self.assertEqual(len(insights.overdue_items(self.svc, as_of="21-02-2025")), 1)
        soon = insights.due_soon_items(self.svc, within_days=7, as_of="20-02-2025")
        self.assertEqual(len(soon), 1)
        self.assertEqual(soon[0].days_overdue, 0)
        self.assertEqual(insights.due_soon_items(self.svc, within_days=7, as_of="01-02-2025"), [])

    def test_sorted_most_overdue_first(self):
        other = self.svc.add_customer("Amit")
        self.add(self.c.id, "UDHAAR", "10-01-2025", "a", "100", due_date="10-02-2025")
        self.add(other.id, "UDHAAR", "10-01-2025", "b", "100", due_date="01-02-2025")
        rows = insights.overdue_items(self.svc, as_of="20-02-2025")
        self.assertEqual([r.customer.name for r in rows], ["Amit", "Rahul"])


# --------------------------------------------------------------------------- trends
class TestTrends(ServiceTestCase):
    def test_monthly_summary_fills_gaps_and_crosses_year_end(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "UDHAAR", "05-11-2025", "a", "100")
        self.add(c.id, "JAMA", "06-11-2025", "b", "40")
        self.add(c.id, "UDHAAR", "20-01-2026", "c", "700")
        self.add(c.id, "UDHAAR", "10-01-2020", "ancient", "999")  # outside the window
        rows = insights.monthly_summary(self.svc, months=4, as_of="15-02-2026")
        self.assertEqual([r.month for r in rows], ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual([(r.udhaar, r.jama) for r in rows], [(10000, 4000), (0, 0), (70000, 0), (0, 0)])

    def test_validation_and_default_window(self):
        self.assertEqual(len(insights.monthly_summary(self.svc)), 12)
        self.assertEqual(insights.monthly_summary(self.svc)[-1].month, "2026-10")
        for bad in (0, 121):
            with self.assertRaises(ValidationError):
                insights.monthly_summary(self.svc, months=bad)

    def test_top_debtors(self):
        names = ["A", "B", "C", "D"]
        for name, amount in zip(names, ("100", "400", "300", "50")):
            c = self.svc.add_customer(name)
            self.add(c.id, "UDHAAR", "10-01-2025", "x", amount)
        paid = self.svc.add_customer("Settled")
        self.add(paid.id, "UDHAAR", "10-01-2025", "x", "10")
        self.add(paid.id, "JAMA", "11-01-2025", "x", "10")
        self.assertEqual([s.customer.name for s in insights.top_debtors(self.svc, 3)], ["B", "C", "A"])
        self.assertEqual(len(insights.top_debtors(self.svc, 100)), 4)


# --------------------------------------------------------------------------- reminders
class TestReminders(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul Sharma", "98765 43210")
        self.add(self.c.id, "UDHAAR", "10-01-2025", "Chain", "5000", due_date="10-02-2025")
        self.add(self.c.id, "JAMA", "15-01-2025", "part", "2000")

    def test_default_message(self):
        self.svc.set_setting("shop_name", "Sharma Jewellers")
        text = insights.reminder_message(self.svc, self.c.id)
        self.assertIn("Rahul Sharma", text)
        self.assertIn("Sharma Jewellers", text)
        self.assertIn("₹3,000.00", text)
        self.assertIn("03-10-2026", text)
        self.assertIn("is past its due date", text)  # the 3,000 left is past its due date
        self.assertNotIn("{", text)

    def test_no_overdue_note_when_not_overdue(self):
        c2 = self.svc.add_customer("Fresh")
        self.add(c2.id, "UDHAAR", "01-10-2026", "x", "100", due_date="01-12-2026")
        self.assertNotIn("due date", insights.reminder_message(self.svc, c2.id))

    def test_requires_positive_balance(self):
        self.add(self.c.id, "JAMA", "16-01-2025", "rest", "3000")
        with self.assertRaises(ValidationError):
            insights.reminder_message(self.svc, self.c.id)
        with self.assertRaises(NotFoundError):
            insights.reminder_message(self.svc, 999)

    def test_custom_template_roundtrip_and_literal_braces(self):
        insights.set_template(self.svc, "Hi {name}, you owe {balance} {unknown} {{x}}")
        self.assertEqual(insights.reminder_message(self.svc, self.c.id),
                         "Hi Rahul Sharma, you owe ₹3,000.00 {unknown} {{x}}")
        insights.set_template(self.svc, "")
        self.assertEqual(insights.get_template(self.svc), insights.DEFAULT_TEMPLATE)
        with self.assertRaises(ValidationError):
            insights.set_template(self.svc, "x" * 1001)

    def test_customer_name_with_braces_is_not_expanded(self):
        weird = self.svc.add_customer("A {balance} B")
        self.add(weird.id, "UDHAAR", "01-10-2026", "x", "10")
        text = insights.reminder_message(self.svc, weird.id)
        self.assertTrue(text.startswith("Dear A {balance} B,"), text)  # name kept literally
        self.assertIn("₹10.00", text)  # the real balance still appears once, where it belongs

    def test_whatsapp_urls(self):
        w = insights.whatsapp_url
        url = w("98765 43210", "Hello\nworld ₹100")
        self.assertTrue(url.startswith("https://wa.me/919876543210?text="))
        self.assertEqual(unquote(url.split("text=")[1]), "Hello\nworld ₹100")
        self.assertIn("%0A", url)
        self.assertEqual(w("+91 98765-43210", "x"), "https://wa.me/919876543210?text=x")
        self.assertEqual(w("098765 43210", "x"), "https://wa.me/919876543210?text=x")
        self.assertEqual(w("9876543210", "x", country_code="+44"), "https://wa.me/449876543210?text=x")
        self.assertIsNone(w("12345", "x"))
        self.assertIsNone(w("", "x"))


# --------------------------------------------------------------------------- PIN
class TestPin(ServiceTestCase):
    def test_set_verify_change_clear(self):
        self.assertFalse(security.has_pin(self.svc))
        code = security.set_pin(self.svc, "1234")
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertTrue(security.has_pin(self.svc))
        self.assertTrue(security.verify_pin(self.svc, "1234"))
        self.assertFalse(security.verify_pin(self.svc, "1235"))
        self.assertFalse(security.verify_pin(self.svc, ""))
        with self.assertRaises(ValidationError):
            security.set_pin(self.svc, "5678")  # changing needs the current PIN
        with self.assertRaises(ValidationError):
            security.set_pin(self.svc, "5678", current_pin="0000")
        security.set_pin(self.svc, "5678", current_pin="1234")
        self.assertTrue(security.verify_pin(self.svc, "5678"))
        self.assertFalse(security.verify_pin(self.svc, "1234"))
        with self.assertRaises(ValidationError):
            security.clear_pin(self.svc, "0000")
        security.clear_pin(self.svc, "5678")
        self.assertFalse(security.has_pin(self.svc))
        self.assertFalse(security.verify_pin(self.svc, "5678"))

    def test_pin_format(self):
        for bad in ("", "123", "12a4", "1234567890123", " 1234"):
            with self.assertRaises(ValidationError):
                security.set_pin(self.svc, bad)
        security.set_pin(self.svc, "123456789012")

    def test_pin_is_stored_hashed_not_in_clear(self):
        security.set_pin(self.svc, "246810")
        stored = self.svc.get_setting(security.PIN_KEY)
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("246810", stored)
        raw = Path(self.db_file).read_bytes()
        self.assertNotIn(b"246810", raw)

    def test_recovery_code(self):
        code = security.set_pin(self.svc, "1234")
        with self.assertRaises(ValidationError):
            security.reset_pin_with_recovery(self.svc, "AAAA-BBBB-CCCC", "9999")
        new_code = security.reset_pin_with_recovery(self.svc, code.lower().replace("-", " "), "9999")
        self.assertTrue(security.verify_pin(self.svc, "9999"))
        self.assertFalse(security.verify_pin(self.svc, "1234"))
        self.assertNotEqual(code, new_code)
        with self.assertRaises(ValidationError):  # the used code is dead
            security.reset_pin_with_recovery(self.svc, code, "1111")
        security.reset_pin_with_recovery(self.svc, new_code, "1111")

    def test_no_recovery_when_no_pin_was_set(self):
        with self.assertRaises(ValidationError):
            security.reset_pin_with_recovery(self.svc, "", "1234")

    def test_guard_locks_after_five_failures_and_doubles(self):
        security.set_pin(self.svc, "1234")
        guard = security.PinGuard(self.svc, clock=self.clock)
        for _ in range(4):
            self.assertFalse(guard.attempt("0000"))
            self.assertEqual(guard.seconds_locked(), 0)
        self.assertFalse(guard.attempt("0000"))  # 5th failure
        self.assertEqual(guard.seconds_locked(), 30)
        self.assertFalse(guard.attempt("1234"))  # right PIN is refused while locked
        self.clock.advance(31)
        self.assertEqual(guard.seconds_locked(), 0)
        self.assertFalse(guard.attempt("0000"))  # 6th failure -> 60 s
        self.assertEqual(guard.seconds_locked(), 60)
        self.clock.advance(61)
        self.assertTrue(guard.attempt("1234"))
        self.assertEqual((guard.failures, guard.locked_until), (0, None))


# --------------------------------------------------------------------------- auto backup
class TestAutoBackup(ServiceTestCase):
    """Automatic backup through the service (details: tests/test_v12_backup_daily.py)."""

    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Rahul")
        self.add(self.c.id, "UDHAAR", "10-01-2025", "Chain", "5000")

    def test_once_per_24h_then_next_day(self):
        first = self.svc.auto_backup_if_due()
        self.assertIsNotNone(first)
        info = backup.validate_backup(first)
        self.assertEqual((info.customers, info.transactions), (1, 1))
        self.assertEqual(first.parent, Path(self.backup_dir))
        self.assertIsNone(self.svc.auto_backup_if_due())
        self.clock.advance(60 * 60 * 24)
        self.assertIsNotNone(self.svc.auto_backup_if_due())
        self.assertEqual(len(list(first.parent.glob("SJB_KHATA_BACKUP_*.db"))), 2)

    def test_custom_folder_and_force_never_overwrite(self):
        target = Path(self.tmp.name) / "OneDrive" / "khata"
        self.svc.backups.set_folder(str(target))
        a = self.svc.auto_backup_if_due()
        b = self.svc.auto_backup_if_due(force=True)  # same second: must not overwrite
        self.assertEqual(a.parent, target)
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())

    def test_unusable_folder_gives_a_clear_error(self):
        blocker = Path(self.tmp.name) / "file.txt"
        blocker.write_text("x")
        self.svc.backups.set_folder(str(blocker / "sub"))
        with self.assertRaises(backup.BackupError):
            self.svc.auto_backup_if_due()