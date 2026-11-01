"""Words shown to the user for the two entry types.

The database and the code keep using the stored values ``UDHAAR`` / ``JAMA`` (so old data and backups keep working).
Only what is *displayed* comes from here – change the words below to rename them everywhere in the app.
"""
from __future__ import annotations

# Accounting convention, from the shop's point of view: what the customer owes is a DEBIT in the customer's
# account, and what the customer pays is a CREDIT to it.
UDHAAR_LABEL = "Debit (Dr)"          # customer took goods / money on credit  (balance goes up)
JAMA_LABEL = "Credit (Cr)"           # customer paid                          (balance goes down)

# Same words for use inside sentences.
UDHAAR_WORD = "debit (dr)"
JAMA_WORD = "credit (cr)"

# Item text used by the Quick Payment dialog (the item field is not asked there).
QUICK_PAYMENT_ITEM = "Payment received"


def type_label(txn_type: str) -> str:
    """Display text for a stored transaction type."""
    return UDHAAR_LABEL if str(txn_type).upper() == "UDHAAR" else JAMA_LABEL
