"""Thermal receipt slip: settings round-trip and the HTML builder (no Qt needed)."""
from types import SimpleNamespace

from tests.helpers import ServiceTestCase
from vx7khata import receipt


def _customer(name="Rahul", mobile="9876543210"):
    return SimpleNamespace(name=name, mobile=mobile)


def _txn(**over):
    base = dict(id=7, txn_date="2026-10-17", item="Atta 5kg", quantity="2", rate_paise=14500,
                amount_paise=29000, txn_type="UDHAAR", notes="", due_date=None)
    base.update(over)
    return SimpleNamespace(**base)


class TestReceiptSetup(ServiceTestCase):
    def test_defaults(self):
        s = receipt.load(self.svc)
        self.assertEqual((s.printer, s.width_mm, s.show_balance), ("", 80, True))

    def test_round_trip(self):
        receipt.save(self.svc, receipt.ReceiptSetup("My Thermal Printer", 58, False))
        s = receipt.load(self.svc)
        self.assertEqual((s.printer, s.width_mm, s.show_balance), ("My Thermal Printer", 58, False))

    def test_bad_width_falls_back_to_default(self):
        receipt.save(self.svc, receipt.ReceiptSetup("", 73, True))   # 73 is not a sold roll width
        self.assertEqual(receipt.load(self.svc).width_mm, receipt.DEFAULT_WIDTH_MM)
        self.svc.set_setting(receipt.SETTING_WIDTH, "nonsense")
        self.assertEqual(receipt.load(self.svc).width_mm, receipt.DEFAULT_WIDTH_MM)


class TestReceiptHtml(ServiceTestCase):
    def html(self, **kw):
        args = dict(shop_name="Shreeji Boxes", shop_details="MG Road\nMob 9876543210",
                    customer=_customer(), txn=_txn(), width_mm=80, balance_paise=None, print_time="")
        args.update(kw)
        return receipt.build_receipt_html(**args)

    def test_contains_shop_customer_and_amounts(self):
        h = self.html()
        self.assertIn("Shreeji Boxes", h)
        self.assertIn("MG Road<br>Mob 9876543210", h)     # newline becomes <br>
        self.assertIn("Rahul", h)
        self.assertIn("9876543210", h)
        self.assertIn("Atta 5kg", h)
        self.assertIn("2 x ", h)                          # qty x rate line
        self.assertIn("145.00", h)                        # rate
        self.assertIn("290.00", h)                        # amount / total
        self.assertIn("17-10-2026", h)
        self.assertIn("Receipt No.", h)
        self.assertIn("Thank you!", h)

    def test_balance_line_wording(self):
        self.assertIn("Balance (to pay)", self.html(balance_paise=50000))
        self.assertIn("Balance (advance)", self.html(balance_paise=-5000))
        self.assertIn("Balance (settled)", self.html(balance_paise=0))
        self.assertNotIn("Balance (", self.html(balance_paise=None))

    def test_due_date_and_notes(self):
        h = self.html(txn=_txn(due_date="2026-11-01", notes="deliver evening"))
        self.assertIn("Promised by", h)
        self.assertIn("01-11-2026", h)
        self.assertIn("deliver evening", h)

    def test_payment_without_quantity_has_no_rate_line(self):
        h = self.html(txn=_txn(quantity="", rate_paise=None, txn_type="JAMA", item="Payment"))
        self.assertNotIn(" x ", h.replace("&nbsp;", " "))
        self.assertNotIn("Promised by", h)

    def test_html_is_escaped(self):
        h = self.html(customer=_customer(name='<script src="x">'), shop_name="A & B <i>")
        self.assertNotIn("<script", h)
        self.assertIn("&lt;script", h)
        self.assertIn("A &amp; B", h)

    def test_widths(self):
        self.assertIn("width:54mm", self.html(width_mm=58))     # 58 - 4
        self.assertIn("font-size:10px", self.html(width_mm=58))
        self.assertIn("width:76mm", self.html(width_mm=80))
        self.assertIn("font-size:11px", self.html(width_mm=80))
        self.assertIn("width:76mm", self.html(width_mm=999))    # unknown width -> default 80
