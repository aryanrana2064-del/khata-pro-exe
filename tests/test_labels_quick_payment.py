import unittest

from tests.helpers import ServiceTestCase
from vx7khata import labels


class TestLabelsAndQuickPayment(ServiceTestCase):
    def test_labels_do_not_use_old_words(self):
        for text in (labels.UDHAAR_LABEL, labels.JAMA_LABEL, labels.UDHAAR_WORD, labels.JAMA_WORD):
            self.assertNotIn("udhaar", text.lower())
            self.assertNotIn("jama", text.lower())
        self.assertEqual(labels.type_label("UDHAAR"), labels.UDHAAR_LABEL)
        self.assertEqual(labels.type_label("JAMA"), labels.JAMA_LABEL)

    def test_payment_with_only_amount_and_default_item(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "UDHAAR", "01-01-2025", "Chain", amount="5000")
        txn = self.add(c.id, "JAMA", "02-01-2025", labels.QUICK_PAYMENT_ITEM, amount="1200.50", notes="UPI")
        self.assertEqual(txn.txn_type, "JAMA")
        self.assertEqual(txn.item, labels.QUICK_PAYMENT_ITEM)
        self.assertEqual(self.svc.customer_balance(c.id), 379950)

    def test_audit_summary_uses_new_words(self):
        c = self.svc.add_customer("Rahul")
        self.add(c.id, "JAMA", "02-01-2025", labels.QUICK_PAYMENT_ITEM, amount="100")
        summaries = [a.summary for a in self.svc.audit_entries()]
        self.assertTrue(any(s.startswith(labels.JAMA_LABEL) for s in summaries))


if __name__ == "__main__":
    unittest.main()
