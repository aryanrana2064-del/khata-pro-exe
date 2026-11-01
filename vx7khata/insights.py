"""Read-only analytics on top of the ledger: aging, overdue, trends, reminders.

Aging uses FIFO settlement: every JAMA (payment) is applied to the customer's oldest unpaid UDHAAR first.
A payment that exceeds all unpaid udhaar is kept as an *advance* and offsets the customer's next udhaar.
Because of that, for every customer:  sum(open items) - advance == udhaar total - jama total (= the balance).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional
from urllib.parse import quote

from . import dates, money
from .service import (
    JAMA, UDHAAR, Customer, CustomerSummary, KhataService, Transaction, ValidationError, _row_to_customer, _row_to_txn,
    _TXN_COLUMNS,
)

BUCKET_LABELS = ("0-30 days", "31-60 days", "61-90 days", "90+ days")
BUCKET_LIMITS = (30, 60, 90)  # upper bounds (in days) of the first three buckets

SETTING_TEMPLATE = "reminder_template"
SETTING_COUNTRY_CODE = "country_code"
DEFAULT_COUNTRY_CODE = "91"

DEFAULT_TEMPLATE = (
    "Dear {name},\n"
    "This is a friendly reminder from {shop} that your outstanding balance as of {date} is {balance}.{overdue_note}\n"
    "Please make the payment at your earliest convenience. Thank you."
)
TEMPLATE_PLACEHOLDERS = ("{name}", "{shop}", "{balance}", "{date}", "{overdue_note}")


# --------------------------------------------------------------------------- FIFO core
@dataclass(frozen=True)
class OpenItem:
    txn: Transaction
    remaining: int  # paise of this udhaar still unpaid


def _fifo(transactions: list[Transaction]) -> tuple[list[OpenItem], int]:
    """Return (unpaid udhaar items oldest first, advance credit in paise)."""
    items: list[list] = []
    credit = 0
    for t in transactions:
        if t.txn_type == UDHAAR:
            remaining = t.amount_paise
            if credit:
                used = min(credit, remaining)
                credit -= used
                remaining -= used
            if remaining:
                items.append([t, remaining])
        else:
            payment = t.amount_paise
            while payment and items:
                used = min(payment, items[0][1])
                items[0][1] -= used
                payment -= used
                if items[0][1] == 0:
                    items.pop(0)
            credit += payment
    return [OpenItem(t, r) for t, r in items], credit


def open_items(svc: KhataService, customer_id: int) -> tuple[list[OpenItem], int]:
    svc.get_customer(customer_id)
    rows = svc.conn.execute(
        f"SELECT {_TXN_COLUMNS} FROM transactions WHERE customer_id = ? ORDER BY txn_date, id", (customer_id,)
    ).fetchall()
    return _fifo([_row_to_txn(r) for r in rows])


def _bucket_index(age_days: int) -> int:
    for i, limit in enumerate(BUCKET_LIMITS):
        if age_days <= limit:
            return i
    return len(BUCKET_LIMITS)


def _as_of(svc: KhataService, as_of) -> date:
    if as_of is None:
        return svc.today()
    try:
        return dates.to_date(dates.parse_date(as_of, allow_future=True))
    except dates.DateError as exc:
        raise ValidationError(str(exc)) from None


def _up_to(transactions: list[Transaction], as_of: date) -> list[Transaction]:
    """Only what had happened by ``as_of``: entries and payments dated later must not change a report 'as on' that day."""
    cutoff = as_of.isoformat()
    return [t for t in transactions if t.txn_date <= cutoff]


def _all_customers_with_transactions(svc: KhataService):
    customers = {
        r["id"]: _row_to_customer(r)
        for r in svc.conn.execute(
            "SELECT id, name, mobile, address, notes, created_at, updated_at, credit_limit_paise FROM customers"
        ).fetchall()
    }
    by_customer: dict[int, list[Transaction]] = {cid: [] for cid in customers}
    for r in svc.conn.execute(f"SELECT {_TXN_COLUMNS} FROM transactions ORDER BY txn_date, id").fetchall():
        by_customer[r["customer_id"]].append(_row_to_txn(r))
    return customers, by_customer


# --------------------------------------------------------------------------- aging
@dataclass(frozen=True)
class AgingRow:
    customer: Customer
    buckets: tuple[int, int, int, int]
    total: int  # unpaid udhaar (sum of buckets)
    advance: int  # unused payments
    oldest_date: Optional[str]  # transaction date of the oldest unpaid udhaar

    @property
    def balance(self) -> int:
        return self.total - self.advance


@dataclass
class AgingReport:
    as_of: str
    rows: list[AgingRow]
    bucket_totals: tuple[int, int, int, int]
    total_outstanding: int
    total_advance: int


def aging_report(svc: KhataService, as_of=None, customer_ids: Optional[list[int]] = None) -> AgingReport:
    today = _as_of(svc, as_of)
    customers, by_customer = _all_customers_with_transactions(svc)
    rows: list[AgingRow] = []
    for cid, customer in customers.items():
        if customer_ids is not None and cid not in customer_ids:
            continue
        items, advance = _fifo(_up_to(by_customer[cid], today))
        if not items and not advance:
            continue
        buckets = [0, 0, 0, 0]
        for item in items:
            age = max(0, (today - dates.to_date(item.txn.txn_date)).days)
            buckets[_bucket_index(age)] += item.remaining
        total = sum(buckets)
        rows.append(AgingRow(customer, tuple(buckets), total, advance, items[0].txn.txn_date if items else None))
    rows.sort(key=lambda r: (-r.total, r.customer.name.casefold()))
    totals = tuple(sum(r.buckets[i] for r in rows) for i in range(4))
    return AgingReport(today.isoformat(), rows, totals, sum(totals), sum(r.advance for r in rows))


# --------------------------------------------------------------------------- overdue
@dataclass(frozen=True)
class OverdueRow:
    customer: Customer
    txn: Transaction
    remaining: int
    days_overdue: int


def overdue_items(svc: KhataService, as_of=None) -> list[OverdueRow]:
    """Unpaid udhaar whose promised due date has passed, most overdue first."""
    today = _as_of(svc, as_of)
    customers, by_customer = _all_customers_with_transactions(svc)
    out: list[OverdueRow] = []
    for cid, customer in customers.items():
        items, _advance = _fifo(_up_to(by_customer[cid], today))
        for item in items:
            if item.txn.due_date and dates.to_date(item.txn.due_date) < today:
                out.append(OverdueRow(customer, item.txn, item.remaining,
                                      (today - dates.to_date(item.txn.due_date)).days))
    out.sort(key=lambda r: (-r.days_overdue, r.customer.name.casefold(), r.txn.id))
    return out


def due_soon_items(svc: KhataService, within_days: int = 7, as_of=None) -> list[OverdueRow]:
    """Unpaid udhaar due today or within the next ``within_days`` days (days_overdue is then <= 0)."""
    today = _as_of(svc, as_of)
    customers, by_customer = _all_customers_with_transactions(svc)
    out: list[OverdueRow] = []
    for cid, customer in customers.items():
        items, _advance = _fifo(_up_to(by_customer[cid], today))
        for item in items:
            if not item.txn.due_date:
                continue
            delta = (dates.to_date(item.txn.due_date) - today).days
            if 0 <= delta <= within_days:
                out.append(OverdueRow(customer, item.txn, item.remaining, -delta))
    out.sort(key=lambda r: (-r.days_overdue, r.customer.name.casefold(), r.txn.id))
    return out


# --------------------------------------------------------------------------- trends / rankings
@dataclass(frozen=True)
class MonthTotals:
    month: str  # YYYY-MM
    udhaar: int
    jama: int


def monthly_summary(svc: KhataService, months: int = 12, as_of=None) -> list[MonthTotals]:
    """Udhaar and jama per calendar month for the last ``months`` months (oldest first), by transaction date."""
    if not 1 <= months <= 120:
        raise ValidationError("months must be between 1 and 120")
    today = _as_of(svc, as_of)
    keys: list[str] = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    rows = svc.conn.execute(
        """SELECT substr(txn_date, 1, 7) AS month,
                  COALESCE(SUM(CASE WHEN txn_type = 'UDHAAR' THEN amount_paise END), 0) AS u,
                  COALESCE(SUM(CASE WHEN txn_type = 'JAMA'   THEN amount_paise END), 0) AS j
             FROM transactions WHERE substr(txn_date, 1, 7) BETWEEN ? AND ? GROUP BY month""",
        (keys[0], keys[-1]),
    ).fetchall()
    found = {r["month"]: (r["u"], r["j"]) for r in rows}
    return [MonthTotals(k, *found.get(k, (0, 0))) for k in keys]


def top_debtors(svc: KhataService, limit: int = 5) -> list[CustomerSummary]:
    owing = [s for s in svc.list_customers() if s.balance > 0]
    owing.sort(key=lambda s: (-s.balance, s.customer.name.casefold()))
    return owing[:max(0, limit)]


def over_limit_customers(svc: KhataService) -> list[CustomerSummary]:
    """Customers whose balance is above their credit limit (limit 0 means unlimited)."""
    out = [s for s in svc.list_customers() if s.customer.credit_limit_paise > 0 and s.balance > s.customer.credit_limit_paise]
    out.sort(key=lambda s: (-(s.balance - s.customer.credit_limit_paise), s.customer.name.casefold()))
    return out


def credit_usage(summary: CustomerSummary) -> Optional[float]:
    """Fraction of the credit limit in use (1.0 = at the limit); None if the customer has no limit."""
    limit = summary.customer.credit_limit_paise
    if limit <= 0:
        return None
    return max(0.0, summary.balance / limit)


# --------------------------------------------------------------------------- reminders
def get_template(svc: KhataService) -> str:
    return svc.get_setting(SETTING_TEMPLATE, "") or DEFAULT_TEMPLATE


def set_template(svc: KhataService, template: str) -> None:
    template = (template or "").strip()
    if len(template) > 1000:
        raise ValidationError("Reminder text is too long (maximum 1000 characters)")
    if not template or template == DEFAULT_TEMPLATE:
        svc.delete_setting(SETTING_TEMPLATE)
    else:
        svc.set_setting(SETTING_TEMPLATE, template)


def render_template(template: str, values: dict[str, str]) -> str:
    """Replace only the known {placeholders}, in ONE pass, so text inserted for one placeholder
    (e.g. a customer called "A {balance} B") is never expanded again. Other braces stay untouched."""
    pattern = re.compile("|".join(re.escape("{" + key + "}") for key in values))
    return pattern.sub(lambda m: values[m.group(0)[1:-1]], template)


def reminder_message(svc: KhataService, customer_id: int, as_of=None) -> str:
    customer = svc.get_customer(customer_id)
    today = _as_of(svc, as_of)
    balance = svc.customer_balance(customer_id)
    if balance <= 0:
        raise ValidationError(f"{customer.name} has no outstanding balance")
    items, _adv = open_items(svc, customer_id)
    overdue_total = sum(i.remaining for i in items if i.txn.due_date and dates.to_date(i.txn.due_date) < today)
    note = f" Of this, {money.format_inr(overdue_total)} is past its due date." if overdue_total else ""
    shop = svc.get_setting("shop_name", "") or "our shop"
    return render_template(get_template(svc), {
        "name": customer.name,
        "shop": shop,
        "balance": money.format_inr(balance),
        "date": dates.format_date(today.isoformat()),
        "overdue_note": note,
    })


def whatsapp_url(mobile: str, text: str, country_code: str = DEFAULT_COUNTRY_CODE) -> Optional[str]:
    """wa.me link for ``mobile`` (national numbers get the country code prefixed); None if no usable number."""
    digits = "".join(ch for ch in (mobile or "") if ch.isdigit())
    cc = "".join(ch for ch in (country_code or "") if ch.isdigit())
    digits = digits.lstrip("0") if len(digits) > 10 else digits
    if len(digits) == 10:
        digits = cc + digits
    if len(digits) < 11:
        return None
    return f"https://wa.me/{digits}?text={quote(text)}"
