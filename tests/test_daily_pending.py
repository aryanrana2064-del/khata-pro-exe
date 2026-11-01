"""Daily Entry rows wait 24 hours, can be deleted meanwhile, then post to the ledger automatically."""
import os
import sqlite3

from vx7khata import backup, db, viewmodel
from vx7khata.service import (
    CreditLimitError, DuplicateEntryError, KhataService, NotFoundError, ValidationError,
)

from .helpers import ServiceTestCase

HOUR = 3600


class TestPendingEntries(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Ram")

    def queue(self, kind="UDHAAR", item="Chain", amount="5000", **kw):
        self.clock.advance(60)  # keeps the duplicate guard out of the way
        return self.svc.add_pending_entry(self.c.id, kind, "03-10-2026", item, amount=amount, **kw)

    def test_pending_row_is_not_in_the_ledger_or_balance(self):
        p = self.queue()
        self.assertEqual(self.svc.customer_balance(self.c.id), 0)
        self.assertEqual(self.svc.ledger(self.c.id).rows, [])
        self.assertEqual(self.svc.dashboard().total_transactions, 0)
        self.assertEqual([x.id for x in self.svc.list_pending()], [p.id])
        self.assertEqual(self.svc.list_pending()[0].customer_name, "Ram")

    def test_not_posted_before_24_hours_posted_after(self):
        p = self.queue()
        self.clock.advance(23 * HOUR)
        self.assertEqual(self.svc.post_due_pending(), [])
        self.assertTrue(0 < self.svc.seconds_until_post(self.svc.get_pending_entry(p.id)) <= HOUR)
        self.clock.advance(HOUR + 60)
        posted = self.svc.post_due_pending()
        self.assertEqual(len(posted), 1)
        self.assertEqual((posted[0].txn_date, posted[0].amount_paise, posted[0].item), ("2026-10-03", 500000, "Chain"))
        self.assertEqual(self.svc.customer_balance(self.c.id), 500000)
        self.assertEqual(self.svc.list_pending(), [])
        self.assertEqual(self.svc.post_due_pending(), [])  # calling again does nothing, no double entry
        self.assertEqual(len(self.svc.ledger(self.c.id).rows), 1)

    def test_delete_before_posting_keeps_it_out_of_the_ledger_for_good(self):
        p = self.queue(item="Payal")
        self.svc.delete_pending(p.id)
        self.assertEqual(self.svc.list_pending(), [])
        self.clock.advance(48 * HOUR)
        self.assertEqual(self.svc.post_due_pending(), [])
        self.assertEqual(self.svc.customer_balance(self.c.id), 0)
        with self.assertRaises(NotFoundError):
            self.svc.delete_pending(p.id)
        actions = [a.action for a in self.svc.audit_entries()]
        self.assertIn("DAILY_ADD", actions)
        self.assertIn("DAILY_DELETE", actions)
        self.assertNotIn("ENTRY_ADD", actions)

    def test_save_now(self):
        p = self.queue()
        txn = self.svc.post_pending(p.id)
        self.assertEqual(self.svc.customer_balance(self.c.id), txn.amount_paise)
        self.assertEqual(self.svc.list_pending(), [])

    def test_only_due_rows_post_and_in_the_right_order(self):
        first = self.queue(item="A", amount="100")
        self.clock.advance(10 * HOUR)
        second = self.queue(item="B", amount="200")
        self.clock.advance(15 * HOUR)  # first is 25h old, second 15h
        posted = self.svc.post_due_pending()
        self.assertEqual([t.item for t in posted], ["A"])
        self.assertEqual([p.id for p in self.svc.list_pending()], [second.id])
        self.assertEqual(self.svc.get_transaction(posted[0].id).item, "A")
        self.assertNotEqual(first.id, second.id)

    def test_payment_rows_work_too(self):
        self.add(self.c.id, "UDHAAR", "01-10-2026", "Old", "1000")
        self.queue(kind="JAMA", item="Payment received", amount="400")
        self.clock.advance(25 * HOUR)
        self.svc.post_due_pending()
        self.assertEqual(self.svc.customer_balance(self.c.id), 60000)

    def test_validation_errors_are_raised_when_queuing(self):
        with self.assertRaises(ValidationError):
            self.svc.add_pending_entry(self.c.id, "UDHAAR", "03-10-2026", "X", amount="1e50")
        with self.assertRaises(ValidationError):
            self.svc.add_pending_entry(self.c.id, "UDHAAR", "03-10-2099", "X", amount="10")  # future date
        self.assertEqual(self.svc.list_pending(), [])

    def test_duplicate_guard(self):
        self.svc.add_pending_entry(self.c.id, "UDHAAR", "03-10-2026", "Chain", amount="5000")
        with self.assertRaises(DuplicateEntryError):
            self.svc.add_pending_entry(self.c.id, "UDHAAR", "03-10-2026", "chain", amount="5000")
        self.svc.add_pending_entry(self.c.id, "UDHAAR", "03-10-2026", "Chain", amount="5000", allow_duplicate=True)
        self.assertEqual(len(self.svc.list_pending()), 2)

    def test_credit_limit_counts_waiting_rows(self):
        c2 = self.svc.add_customer("Shyam", credit_limit="1000")
        self.clock.advance(60)
        self.svc.add_pending_entry(c2.id, "UDHAAR", "03-10-2026", "A", amount="700")
        self.clock.advance(60)
        with self.assertRaises(CreditLimitError):
            self.svc.add_pending_entry(c2.id, "UDHAAR", "03-10-2026", "B", amount="400")
        self.clock.advance(60)
        self.svc.add_pending_entry(c2.id, "UDHAAR", "03-10-2026", "B", amount="400", allow_over_limit=True)

    def test_customer_with_waiting_rows_cannot_be_deleted(self):
        p = self.queue()
        with self.assertRaises(ValidationError):
            self.svc.delete_customer(self.c.id)
        self.svc.delete_pending(p.id)
        self.svc.delete_customer(self.c.id)

    def test_waiting_rows_survive_backup_and_restore(self):
        p = self.queue()
        dest = os.path.join(self.tmp.name, "b.db")
        self.svc.create_backup(dest)
        self.assertEqual(backup.validate_backup(dest).schema_version, db.SCHEMA_VERSION)
        self.svc.delete_pending(p.id)
        self.svc.restore_backup(dest)
        self.assertEqual([x.item for x in self.svc.list_pending()], ["Chain"])

    def test_old_database_is_upgraded_and_gets_the_table(self):
        path = os.path.join(self.tmp.name, "old.db")
        self.svc.close()
        os.replace(self.db_file, path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE pending_entries")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()
        svc = KhataService(path, clock=self.clock)
        self.addCleanup(svc.close)
        self.assertEqual(svc.list_pending(), [])
        self.assertEqual(db.get_user_version(svc.conn), db.SCHEMA_VERSION)


class TestTimeLeftText(ServiceTestCase):
    def test_texts(self):
        self.assertEqual(viewmodel.time_left_text(0), "saving now")
        self.assertEqual(viewmodel.time_left_text(-5), "saving now")
        self.assertEqual(viewmodel.time_left_text(1), "1 min")
        self.assertEqual(viewmodel.time_left_text(12 * 60), "12 min")
        self.assertEqual(viewmodel.time_left_text(HOUR), "1h 00m")
        self.assertEqual(viewmodel.time_left_text(5 * HOUR + 20 * 60), "5h 20m")
        self.assertEqual(viewmodel.time_left_text(24 * HOUR), "24h 00m")
