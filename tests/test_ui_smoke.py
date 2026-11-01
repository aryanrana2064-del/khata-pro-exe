"""UI smoke test. Runs only where PySide6 is installed (it is skipped otherwise).

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui_smoke -v
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestUiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from vx7khata.service import KhataService
        from vx7khata.ui.main_window import MainWindow

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.svc = KhataService(os.path.join(self.tmp.name, "khata.db"))
        self.addCleanup(self.svc.close)
        self.win = MainWindow(self.svc)
        self.addCleanup(self.win.close)
        self.messages = []
        self.answers = []  # scripted answers for confirm()
        self.win.warn = lambda title, text: self.messages.append(("warn", text))
        self.win.info = lambda title, text: self.messages.append(("info", text))
        self.win.confirm = lambda *a, **k: self.answers.pop(0) if self.answers else True

    def _select_customer(self, customer_id):
        self.win.entry_form.select_customer(customer_id)

    def _enter(self, kind, qdate, item, amount):
        form = self.win.entry_form
        (form.udhaar if kind == "UDHAAR" else form.jama).setChecked(True)
        form.date.setDate(qdate)
        form.item.setText(item)
        form.amount.setText(amount)
        self.win._save_entry()

    def _show_ledger(self, customer_id):
        self.win.ledger_customer.setCurrentIndex(self.win.ledger_customer.findData(customer_id))
        self.win._refresh_ledger()

    def test_quick_payment_needs_only_amount(self):
        from vx7khata.labels import JAMA_LABEL, QUICK_PAYMENT_ITEM
        from vx7khata.ui.dialogs import QuickPaymentDialog

        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "01-01-2025", "Chain", amount="5000")
        dialog = QuickPaymentDialog(self.svc, self.win._add_with_prompts, customer_id=c.id)
        self.addCleanup(dialog.close)
        dialog.amount.setText("1500")
        dialog._save()
        self.assertIsNotNone(dialog.saved)
        self.assertEqual(dialog.saved.txn_type, "JAMA")
        self.assertEqual(dialog.saved.item, QUICK_PAYMENT_ITEM)
        self.assertEqual(self.svc.customer_balance(c.id), 350000)
        self.assertEqual(JAMA_LABEL, "Credit (Cr)")

    def test_quick_payment_without_amount_is_rejected(self):
        from vx7khata.ui.dialogs import QuickPaymentDialog

        c = self.svc.add_customer("Rahul")
        shown = []
        dialog = QuickPaymentDialog(self.svc, self.win._add_with_prompts, customer_id=c.id)
        self.addCleanup(dialog.close)
        import vx7khata.ui.dialogs as dlg
        original = dlg.QMessageBox.warning
        dlg.QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[2]))
        self.addCleanup(lambda: setattr(dlg.QMessageBox, "warning", original))
        dialog._save()
        self.assertIsNone(dialog.saved)
        self.assertTrue(shown)
        self.assertEqual(self.svc.customer_balance(c.id), 0)

    def test_empty_window(self):
        self.assertEqual(self.win.cards["customers"].value.text(), "0")
        self.assertEqual(self.win.cust_table.rowCount(), 0)

    def test_backdated_entries_through_the_form(self):
        c = self.svc.add_customer("Rahul")
        self.win.refresh_all()
        self._select_customer(c.id)
        self._enter("UDHAAR", QDate(2025, 1, 10), "Old udhaar", "5000")
        self._enter("JAMA", QDate(2025, 1, 15), "Payment", "2000")
        self._enter("UDHAAR", QDate(2025, 1, 12), "Extra", "1000")
        self.assertEqual(self.messages, [])

        self._show_ledger(c.id)
        table = self.win.ledger_table
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual([table.item(r, 0).text() for r in range(3)], ["10-01-2025", "12-01-2025", "15-01-2025"])
        self.assertEqual([table.item(r, 6).text() for r in range(3)], ["5,000.00", "6,000.00", "4,000.00"])
        self.assertEqual(self.win.cards["net"].value.text(), "₹4,000.00")
        self.assertEqual(self.win.cards["historical"].value.text(), "3")
        self.assertEqual(self.win.cust_table.item(0, 5).text(), "₹4,000.00")

    def test_quantity_times_rate_autofills_amount(self):
        form = self.win.entry_form
        form.quantity.setText("3")
        form.rate.setText("200")
        self.assertEqual(form.amount.text(), "600.00")
        self.assertTrue(form.amount.isReadOnly())

    def test_duplicate_submission_asks_before_saving_again(self):
        c = self.svc.add_customer("Rahul")
        self.win.refresh_all()
        self._select_customer(c.id)
        self._enter("UDHAAR", QDate(2025, 1, 10), "Chain", "100")
        self.answers = [False]  # user declines the "possible duplicate" prompt
        self._enter("UDHAAR", QDate(2025, 1, 10), "Chain", "100")
        self.assertEqual(len(self.svc.ledger(c.id).rows), 1)

    def test_delete_requires_confirmation_and_recalculates(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "A", amount="100")
        self.svc.add_transaction(c.id, "UDHAAR", "11-01-2025", "B", amount="200")
        self.win.refresh_all()
        self._show_ledger(c.id)
        self.win.ledger_table.selectRow(0)
        self.answers = [False]
        self.win._delete_entry()
        self.assertEqual(len(self.svc.ledger(c.id).rows), 2)  # declined: nothing deleted
        self.answers = [True]
        self.win._delete_entry()
        self.assertEqual(self.svc.ledger(c.id).closing_balance, 20000)
        self.assertEqual(self.win.ledger_table.rowCount(), 1)

    # ------------------------------------------------------------ premium features
    def test_credit_limit_prompts_and_can_be_declined_or_accepted(self):
        c = self.svc.add_customer("Rahul", credit_limit="1000")
        self.win.refresh_all()
        self._select_customer(c.id)
        self._enter("UDHAAR", QDate(2025, 1, 10), "Chain", "800")
        self.assertEqual(self.svc.customer_balance(c.id), 80000)

        self.answers = [False]  # decline "credit limit exceeded"
        self._enter("UDHAAR", QDate(2025, 1, 11), "Ring", "500")
        self.assertEqual(self.svc.customer_balance(c.id), 80000)

        self.answers = [True]  # accept it
        self._enter("UDHAAR", QDate(2025, 1, 11), "Ring", "500")
        self.assertEqual(self.svc.customer_balance(c.id), 130000)
        self.assertEqual(self.messages, [])

    def test_customer_table_shows_credit_usage(self):
        c = self.svc.add_customer("Rahul", credit_limit="1000")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="900")
        self.win.refresh_all()
        self.assertIn("90% used", self.win.cust_table.item(0, 6).text())

    def test_overdue_payment_appears_in_recovery_and_dashboard(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="1000", due_date="10-02-2025")
        self.win.refresh_all()
        self.win.show_page("recovery")
        self.assertEqual(self.win.overdue_table.rowCount(), 1)
        self.assertEqual(self.win.aging_table.rowCount(), 1)
        self.assertEqual(self.win.aging_table.item(0, 5).text(), "1,000.00")  # 90+ days bucket
        self.assertEqual(self.win.aging_table.item(0, 6).text(), "1,000.00")  # outstanding
        self.assertIn("1 overdue payment", self.win.attention_label.text())
        self.assertEqual(self.win.debtor_table.item(0, 0).text(), "Rahul")

    def test_payment_reduces_aging_oldest_first(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Old", amount="1000")
        self.svc.add_transaction(c.id, "JAMA", "11-01-2025", "Part payment", amount="400")
        self.win.refresh_all()
        self.win.show_page("recovery")
        self.assertEqual(self.win.aging_table.item(0, 6).text(), "600.00")

    def test_activity_page_lists_changes_newest_first(self):
        c = self.svc.add_customer("Rahul")
        self.svc.add_transaction(c.id, "UDHAAR", "10-01-2025", "Chain", amount="100")
        self.win.show_page("activity")
        table = self.win.audit_table
        actions = [table.item(r, 1).text() for r in range(table.rowCount())]
        self.assertEqual(actions[0], "Entry added")
        self.assertIn("Customer added", actions)

    def test_settings_save_and_theme_toggle(self):
        from vx7khata.ui import theme

        self.addCleanup(theme.set_current, "light")
        self.win.show_page("settings")
        self.win.shop_edit.setText("  My   Shop ")
        self.win.country_edit.setText("91")
        self.win._save_business()
        self.assertEqual(self.svc.get_setting("shop_name"), "My Shop")

        self.win.country_edit.setText("")  # invalid
        self.win._save_business()
        self.assertTrue(any(kind == "warn" for kind, _ in self.messages))

        self.win._toggle_theme()
        self.assertEqual(self.svc.get_setting("theme"), "dark")
        self.win._toggle_theme()
        self.assertEqual(self.svc.get_setting("theme"), "light")

    def test_reminder_for_settled_customer_shows_info_not_dialog(self):
        c = self.svc.add_customer("Rahul")
        self.win.refresh_all()
        self.win._show_reminder(c.id)
        self.assertEqual(self.messages[-1][0], "info")

    def test_lock_without_pin_explains_instead_of_locking(self):
        self.win.lock_now()
        self.assertEqual(self.messages[-1][0], "info")

    def test_navigation_shows_every_page(self):
        for key in self.win.page_index:
            self.win.show_page(key)
            self.assertEqual(self.win.stack.currentIndex(), self.win.page_index[key])


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class TestDateField(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_bad_date_is_flagged_not_replaced(self):
        from vx7khata.ui.widgets import DateField
        f = DateField()
        f.edit.setText("31-02-2026")
        self.assertEqual(f.text_dmy(), "31-02-2026")        # typed text goes on, so saving reports it
        self.assertTrue(f.edit.property("invalid"))
        f.edit.setText("08-10-2020")
        self.assertEqual(f.text_dmy(), "08-10-2020")
        self.assertFalse(f.edit.property("invalid"))

    def test_a_future_date_is_flagged_not_replaced(self):
        from vx7khata.ui.widgets import DateField
        f = DateField()
        future = QDate.currentDate().addDays(5).toString("dd-MM-yyyy")
        f.edit.setText(future)
        self.assertEqual(f.text_dmy(), future)
        self.assertTrue(f.edit.property("invalid"))

    def test_untouched_box_moves_to_the_new_day_after_midnight(self):
        from vx7khata.ui.widgets import DateField
        f = DateField()
        yesterday = QDate.currentDate().addDays(-1)
        f._max = yesterday
        f._date = yesterday
        f._followed_today = yesterday
        f.refresh_limits()
        self.assertEqual(f.date(), QDate.currentDate())

    def test_a_date_the_person_picked_is_not_moved(self):
        from vx7khata.ui.widgets import DateField
        f = DateField()
        picked = QDate.currentDate().addDays(-3)
        f.edit.setText(picked.toString("dd-MM-yyyy"))
        f.date()
        f._followed_today = QDate.currentDate().addDays(-1)
        f.refresh_limits()
        self.assertEqual(f.date(), picked)


if __name__ == "__main__":
    unittest.main()
