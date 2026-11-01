"""Regression tests for the issues found in the code review."""
from vx7khata import labels, money, security
from vx7khata.service import CreditLimitError, KhataService, ValidationError

from .helpers import ServiceTestCase


class TestMoneyEdgeCases(ServiceTestCase):
    def test_huge_or_odd_numbers_raise_money_error_not_a_crash(self):
        for bad in ("1e50", "1E+30", "9" * 40, "1_000", "\u0663\u0664", "5e-1", "abc", "1" * 19):
            with self.assertRaises(money.MoneyError, msg=bad):
                money.rupees_to_paise(bad)
        for bad in ("1e50", "1e400", "1e9", "1000000001"):
            with self.assertRaises(money.MoneyError, msg=bad):
                money.parse_quantity(bad)

    def test_normal_inputs_still_work(self):
        self.assertEqual(money.rupees_to_paise("1,234.50"), 123450)
        self.assertEqual(money.rupees_to_paise("Rs. 10"), 1000)
        self.assertEqual(money.rupees_to_paise(".5"), 50)
        self.assertEqual(str(money.parse_quantity("0.125")), "0.125")

    def test_service_turns_huge_amount_into_validation_error(self):
        c = self.svc.add_customer("Ram")
        with self.assertRaises(ValidationError):
            self.svc.add_transaction(c.id, "UDHAAR", "03-10-2026", "X", amount="1e50")


class TestEditCreditLimit(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.c = self.svc.add_customer("Ram", credit_limit="1000")
        self.t = self.add(self.c.id, "UDHAAR", "03-10-2026", "Atta", "500")

    def test_edit_that_goes_over_limit_is_refused_until_confirmed(self):
        with self.assertRaises(CreditLimitError):
            self.svc.update_transaction(self.t.id, "UDHAAR", "03-10-2026", "Atta", amount="50000")
        self.assertEqual(self.svc.customer_balance(self.c.id), 50000)  # unchanged: still the original 500.00
        self.svc.update_transaction(self.t.id, "UDHAAR", "03-10-2026", "Atta", amount="50000", allow_over_limit=True)
        self.assertEqual(self.svc.customer_balance(self.c.id), 5000000)

    def test_payment_changed_to_credit_is_checked_too(self):
        pay = self.add(self.c.id, "JAMA", "03-10-2026", "Payment", "400")
        with self.assertRaises(CreditLimitError):
            self.svc.update_transaction(pay.id, "UDHAAR", "03-10-2026", "Payment", amount="900")

    def test_edit_within_limit_and_note_edit_on_over_limit_customer_work(self):
        self.svc.update_transaction(self.t.id, "UDHAAR", "03-10-2026", "Atta", amount="900")
        big = self.svc.update_transaction(self.t.id, "UDHAAR", "03-10-2026", "Atta", amount="5000",
                                          allow_over_limit=True)
        # fixing only the note of an already over-limit entry must not be blocked
        fixed = self.svc.update_transaction(big.id, "UDHAAR", "03-10-2026", "Atta", amount="5000", notes="typo fix")
        self.assertEqual(fixed.notes, "typo fix")
        # lowering the amount is always fine
        self.svc.update_transaction(big.id, "UDHAAR", "03-10-2026", "Atta", amount="3000")


class TestLabels(ServiceTestCase):
    def test_accounting_convention(self):
        self.assertEqual(labels.type_label("UDHAAR"), "Debit (Dr)")
        self.assertEqual(labels.type_label("JAMA"), "Credit (Cr)")


class TestPinGuardPersists(ServiceTestCase):
    def test_lockout_survives_restart(self):
        security.set_pin(self.svc, "1234")
        guard = security.PinGuard(self.svc, clock=self.clock)
        for _ in range(5):
            guard.attempt("0000")
        self.assertEqual(guard.seconds_locked(), 30)
        self.svc.close()
        self.svc = KhataService(self.db_file, clock=self.clock)  # "app restarted"
        guard2 = security.PinGuard(self.svc, clock=self.clock)
        self.assertEqual(guard2.failures, 5)
        self.assertEqual(guard2.seconds_locked(), 30)
        self.assertFalse(guard2.attempt("1234"))
        self.clock.advance(31)
        self.assertTrue(guard2.attempt("1234"))
        self.assertEqual(security.PinGuard(self.svc, clock=self.clock).failures, 0)

    def test_recovery_reset_lifts_lockout(self):
        code = security.set_pin(self.svc, "1234")
        guard = security.PinGuard(self.svc, clock=self.clock)
        for _ in range(5):
            guard.attempt("0000")
        security.reset_pin_with_recovery(self.svc, code, "4321")
        self.assertEqual(security.PinGuard(self.svc, clock=self.clock).seconds_locked(), 0)


class TestDeleteCustomer(ServiceTestCase):
    def test_customer_with_entries_cannot_be_deleted(self):
        c = self.svc.add_customer("Ram")
        self.add(c.id, "UDHAAR", "03-10-2026", "Atta", "500")
        with self.assertRaises(ValidationError):
            self.svc.delete_customer(c.id)
        self.assertEqual(self.svc.get_customer(c.id).name, "Ram")

    def test_empty_customer_can_be_deleted(self):
        c = self.svc.add_customer("Shyam")
        self.svc.delete_customer(c.id)
        self.assertEqual(self.svc.list_customers(), [])
