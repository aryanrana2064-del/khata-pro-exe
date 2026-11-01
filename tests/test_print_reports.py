"""Printable HTML for every report (shown through the Windows print dialog)."""
from tests.helpers import ServiceTestCase
from vx7khata import exports, insights


class TestPrintReportsHtml(ServiceTestCase):
    def setUp(self):
        super().setUp()
        a = self.svc.add_customer("Rahul <b>", "9876543210")
        b = self.svc.add_customer("Amit")
        self.add(a.id, "UDHAAR", "01-11-2024", "Old chain", "200")
        self.add(a.id, "UDHAAR", "10-01-2025", "Ring", "1000", due_date="10-02-2025")
        self.add(a.id, "JAMA", "05-02-2025", "part", "300")
        self.add(b.id, "JAMA", "01-01-2025", "advance", "250")

    def test_aging_and_overdue(self):
        report = insights.aging_report(self.svc, as_of="20-02-2025")
        html = exports.aging_html(report, "My Shop")
        self.assertIn("My Shop", html)
        self.assertIn("Outstanding Aging Report", html)
        self.assertIn("Rahul &lt;b&gt;", html)           # customer text is escaped
        self.assertNotIn("Rahul <b>", html)
        rows = insights.overdue_items(self.svc, as_of="20-02-2025")
        self.assertIn("Overdue Payments", exports.overdue_html(rows, "2025-02-20", "My Shop"))

    def test_register_and_balances(self):
        reg = self.svc.register(search="", customer_ids=None)
        html = exports.register_html(reg, "My Shop", "All customers")
        self.assertIn("Transaction Register", html)
        self.assertIn("Ring", html)
        summaries = self.svc.list_customers("")
        html = exports.customers_html(summaries, "My Shop", "")
        self.assertIn("Overall total", html)
        self.assertIn("Amit", html)

    def test_over_limit_and_activity(self):
        self.assertIn("Over Credit Limit", exports.over_limit_html([], "My Shop"))
        entries = self.svc.audit_entries(50)
        html = exports.activity_html(entries, "My Shop")
        self.assertIn("Activity Log", html)
        self.assertIn("<td>", html)

    def test_empty_report_still_prints(self):
        self.assertIn("No entries", exports.activity_html([], "My Shop"))
