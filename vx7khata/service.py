"""Ledger logic: customers, item-wise entries, balances, summaries.

Every total and every running balance is computed from the saved rows on
demand, so adding, editing or deleting a back-dated entry can never leave a
stale balance behind.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import backup as backup_mod
from .autobackup import BackupManager
from . import db as dbmod
from . import dates, money
from .labels import JAMA_LABEL, UDHAAR_LABEL, UDHAAR_WORD, type_label

UDHAAR = "UDHAAR"
JAMA = "JAMA"
TXN_TYPES = (UDHAAR, JAMA)

DUPLICATE_WINDOW_SECONDS = 10
PENDING_HOURS = 24  # Daily Entry rows are saved into the ledger this long after they were typed
MAX_NAME = 120
MAX_ITEM = 200
MAX_TEXT = 2000


class KhataError(Exception):
    """Base class for errors that are safe to show to the user."""


class ValidationError(KhataError):
    pass


class NotFoundError(KhataError):
    pass


class DuplicateEntryError(KhataError):
    """An identical entry was saved a few seconds ago (likely a double click)."""


class CreditLimitError(KhataError):
    """The new UDHAAR would take the customer's balance above their credit limit."""

    def __init__(self, message: str, limit_paise: int, new_balance_paise: int):
        super().__init__(message)
        self.limit_paise = limit_paise
        self.new_balance_paise = new_balance_paise


# --------------------------------------------------------------------------- models
@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    mobile: str
    address: str
    notes: str
    created_at: str
    updated_at: str
    credit_limit_paise: int = 0  # 0 = no limit


@dataclass(frozen=True)
class CustomerSummary:
    customer: Customer
    total_udhaar: int
    total_jama: int
    txn_count: int
    last_txn_date: Optional[str]

    @property
    def balance(self) -> int:
        return self.total_udhaar - self.total_jama


@dataclass(frozen=True)
class Transaction:
    id: int
    customer_id: int
    txn_date: str  # ISO YYYY-MM-DD: the date the transaction happened
    item: str
    quantity: Optional[str]
    rate_paise: Optional[int]
    amount_paise: int
    txn_type: str
    notes: str
    created_at: str  # when the row was really entered (audit; never the txn date)
    updated_at: str
    due_date: Optional[str] = None  # promised payment date (UDHAAR only), ISO


@dataclass(frozen=True)
class LedgerRow:
    txn: Transaction
    udhaar: int
    jama: int
    balance: int  # true running balance after this entry


@dataclass
class Ledger:
    customer: Customer
    date_from: Optional[str]
    date_to: Optional[str]
    search: str
    opening_balance: int
    rows: list[LedgerRow]
    total_udhaar: int  # of the rows shown
    total_jama: int  # of the rows shown
    closing_balance: int  # true balance at the end of the selected period
    entries_in_period: int  # all entries in the date range, before item search


@dataclass(frozen=True)
class RegisterRow:
    txn: Transaction
    customer_name: str


@dataclass
class Register:
    date_from: Optional[str]
    date_to: Optional[str]
    search: str
    customer_ids: Optional[list[int]]
    rows: list[RegisterRow]
    total_udhaar: int
    total_jama: int

    @property
    def net(self) -> int:
        return self.total_udhaar - self.total_jama


@dataclass(frozen=True)
class DashboardSummary:
    total_customers: int
    total_udhaar: int
    total_jama: int
    todays_transactions: int  # entries dated today
    historical_transactions: int  # entries dated before today
    total_transactions: int

    @property
    def net_outstanding(self) -> int:
        return self.total_udhaar - self.total_jama


@dataclass(frozen=True)
class PendingEntry:
    """A Daily Entry row waiting to be saved into the customer's ledger (it is not part of any balance yet)."""
    id: int
    customer_id: int
    customer_name: str
    txn_type: str
    txn_date: str  # ISO: the date the transaction happened
    item: str
    quantity: Optional[str]
    rate_paise: Optional[int]
    amount_paise: int
    notes: str
    due_date: Optional[str]
    created_at: str  # when it was typed in
    post_at: str  # when it will be saved into the ledger automatically


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    action: str
    entity: str
    entity_id: Optional[int]
    customer_id: Optional[int]
    summary: str
    details: str  # JSON text, may be empty


# --------------------------------------------------------------------------- helpers
def _clean_single_line(value, label: str, max_len: int, required: bool = True) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValidationError(f"{label} is required")
    if len(text) > max_len:
        raise ValidationError(f"{label} is too long (maximum {max_len} characters)")
    return text


def _clean_multiline(value, label: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_TEXT:
        raise ValidationError(f"{label} is too long (maximum {MAX_TEXT} characters)")
    return text


def _clean_mobile(value) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if not re.fullmatch(r"[0-9+\-() ]+", text):
        raise ValidationError("Mobile number can only contain digits, spaces, + - ( )")
    digit_count = len(re.sub(r"\D", "", text))
    if not 7 <= digit_count <= 15:
        raise ValidationError("Mobile number must have 7 to 15 digits")
    return text


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


BASIS_TXN_DATE = "txn_date"   # entries whose transaction date is the chosen day (default)
BASIS_ENTERED = "entered"     # entries that were typed in on the chosen day (includes back-dated ones)


@dataclass(frozen=True)
class DailyRow:
    txn_id: int
    customer_id: int
    customer_name: str
    txn_type: str
    amount_paise: int
    txn_date: str      # ISO date of the transaction
    time: str          # HH:MM:SS the row was really entered (never changed by an edit)
    entered_date: str  # ISO date the row was really entered
    item: str
    notes: str


@dataclass(frozen=True)
class DailySummary:
    day: str
    basis: str
    total_jama: int
    total_udhaar: int
    transaction_count: int
    customer_count: int
    rows: list

    @property
    def net_difference(self) -> int:
        """Jama minus Udhaar for the day. NOT the cash balance and NOT any customer's outstanding balance."""
        return self.total_jama - self.total_udhaar


_DAILY_SORTS = {
    "name": lambda r: (r.customer_name.casefold(), r.time, r.txn_id),
    "amount": lambda r: (r.amount_paise, r.time, r.txn_id),
    "time": lambda r: (r.time, r.txn_id),
}


def filter_daily_rows(rows, txn_type: Optional[str] = None, search: str = "", sort: str = "time",
                      descending: bool = False) -> list:
    """Filter Daily Summary rows by type (UDHAAR / JAMA), customer-name search, and sort them."""
    if sort not in _DAILY_SORTS:
        raise ValidationError("Sort by name, amount or time")
    needle = (search or "").strip().casefold()
    out = [r for r in rows
           if (not txn_type or r.txn_type == txn_type) and (not needle or needle in r.customer_name.casefold())]
    return sorted(out, key=_DAILY_SORTS[sort], reverse=descending)


_TXN_COLUMNS = (
    "id, customer_id, txn_date, item, quantity, rate_paise, amount_paise, "
    "txn_type, notes, created_at, updated_at, due_date"
)


def _row_to_customer(r: sqlite3.Row) -> Customer:
    return Customer(r["id"], r["name"], r["mobile"], r["address"], r["notes"], r["created_at"], r["updated_at"],
                    r["credit_limit_paise"])


def _row_to_txn(r: sqlite3.Row) -> Transaction:
    return Transaction(
        r["id"], r["customer_id"], r["txn_date"], r["item"], r["quantity"], r["rate_paise"],
        r["amount_paise"], r["txn_type"], r["notes"], r["created_at"], r["updated_at"], r["due_date"],
    )


_PENDING_SELECT = (
    "SELECT p.id, p.customer_id, c.name AS customer_name, p.txn_type, p.txn_date, p.item, p.quantity, p.rate_paise, "
    "p.amount_paise, p.notes, p.due_date, p.created_at, p.post_at "
    "FROM pending_entries p JOIN customers c ON c.id = p.customer_id"
)


def _row_to_pending(r: sqlite3.Row) -> PendingEntry:
    return PendingEntry(r["id"], r["customer_id"], r["customer_name"], r["txn_type"], r["txn_date"], r["item"],
                        r["quantity"], r["rate_paise"], r["amount_paise"], r["notes"], r["due_date"],
                        r["created_at"], r["post_at"])


_SIGNED_AMOUNT = "CASE txn_type WHEN 'UDHAAR' THEN amount_paise ELSE -amount_paise END"


# --------------------------------------------------------------------------- service
class KhataService:
    def __init__(self, db_path, clock: Optional[Callable[[], datetime]] = None):
        self.db_path = Path(db_path)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.conn = dbmod.open_database(self.db_path)
        self._backups: Optional[BackupManager] = None

    def close(self) -> None:
        self.conn.close()

    # ---- time -------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def today(self) -> date:
        return self._clock().date()

    def _now_iso(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    # ---- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with dbmod.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with dbmod.transaction(self.conn):
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    # ---- audit trail ------------------------------------------------------
    def _audit(self, action: str, entity: str, entity_id: Optional[int], customer_id: Optional[int],
               summary: str, details: Optional[dict] = None) -> None:
        """Append to the activity log. Must be called inside the same DB transaction as the change itself."""
        self.conn.execute(
            "INSERT INTO audit_log(at, action, entity, entity_id, customer_id, summary, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._now_iso(), action, entity, entity_id, customer_id, summary,
             json.dumps(details, ensure_ascii=False, sort_keys=True) if details else ""),
        )

    def audit_entries(self, limit: int = 500, customer_id: Optional[int] = None, search: str = "") -> list[AuditEntry]:
        sql = "SELECT id, at, action, entity, entity_id, customer_id, summary, details FROM audit_log WHERE 1=1"
        params: list = []
        if customer_id is not None:
            sql += " AND customer_id = ?"
            params.append(customer_id)
        needle = " ".join(search.split())
        if needle:
            sql += " AND (summary LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\')"
            like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend([like, like])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [AuditEntry(r["id"], r["at"], r["action"], r["entity"], r["entity_id"], r["customer_id"],
                           r["summary"], r["details"]) for r in self.conn.execute(sql, params).fetchall()]

    def _txn_summary(self, t: Transaction, customer_name: str) -> str:
        return (f"{type_label(t.txn_type)} {money.format_inr(t.amount_paise)} - {t.item} "
                f"(dated {dates.format_date(t.txn_date)}) - {customer_name}")

    # ---- customers --------------------------------------------------------
    @staticmethod
    def _parse_credit_limit(value) -> int:
        if _is_blank(value) or str(value).strip().replace(",", "") in ("0", "0.0", "0.00"):
            return 0
        try:
            return money.rupees_to_paise(value, "Credit limit")
        except money.MoneyError as exc:
            raise ValidationError(str(exc)) from None

    def add_customer(self, name, mobile="", address="", notes="", credit_limit="") -> Customer:
        name = _clean_single_line(name, "Customer name", MAX_NAME)
        mobile = _clean_mobile(mobile)
        address = _clean_multiline(address, "Address")
        notes = _clean_multiline(notes, "Notes")
        limit = self._parse_credit_limit(credit_limit)
        now = self._now_iso()
        try:
            with dbmod.transaction(self.conn):
                cur = self.conn.execute(
                    "INSERT INTO customers(name, name_key, mobile, address, notes, created_at, updated_at, "
                    "credit_limit_paise) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, name.casefold(), mobile, address, notes, now, now, limit),
                )
                self._audit("CUSTOMER_ADD", "customer", cur.lastrowid, cur.lastrowid, f"Added customer '{name}'",
                            {"credit_limit_paise": limit} if limit else None)
        except sqlite3.IntegrityError:
            raise ValidationError(f"A customer named '{name}' already exists") from None
        return self.get_customer(cur.lastrowid)

    def update_customer(self, customer_id: int, name, mobile="", address="", notes="", credit_limit=None) -> Customer:
        """``credit_limit=None`` keeps the current limit; '' or 0 removes it."""
        old = self.get_customer(customer_id)
        name = _clean_single_line(name, "Customer name", MAX_NAME)
        mobile = _clean_mobile(mobile)
        address = _clean_multiline(address, "Address")
        notes = _clean_multiline(notes, "Notes")
        limit = old.credit_limit_paise if credit_limit is None else self._parse_credit_limit(credit_limit)
        changes = {}
        for field_name, before, after in (("name", old.name, name), ("mobile", old.mobile, mobile),
                                          ("address", old.address, address), ("notes", old.notes, notes),
                                          ("credit_limit_paise", old.credit_limit_paise, limit)):
            if before != after:
                changes[field_name] = [before, after]
        try:
            with dbmod.transaction(self.conn):
                self.conn.execute(
                    "UPDATE customers SET name=?, name_key=?, mobile=?, address=?, notes=?, credit_limit_paise=?, "
                    "updated_at=? WHERE id=?",
                    (name, name.casefold(), mobile, address, notes, limit, self._now_iso(), customer_id),
                )
                if changes:
                    self._audit("CUSTOMER_EDIT", "customer", customer_id, customer_id,
                                f"Edited customer '{old.name}'" + (f" (now '{name}')" if name != old.name else ""),
                                changes)
        except sqlite3.IntegrityError:
            raise ValidationError(f"A customer named '{name}' already exists") from None
        return self.get_customer(customer_id)

    def get_customer(self, customer_id: int) -> Customer:
        row = self.conn.execute(
            "SELECT id, name, mobile, address, notes, created_at, updated_at, credit_limit_paise "
            "FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("Customer not found")
        return _row_to_customer(row)

    def delete_customer(self, customer_id: int) -> None:
        """Only customers with no entries can be deleted (their ledger is never destroyed silently)."""
        customer = self.get_customer(customer_id)
        try:
            with dbmod.transaction(self.conn):
                count = self.conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE customer_id = ?", (customer_id,)
                ).fetchone()[0]
                if count:
                    raise ValidationError(
                        f"{customer.name} has {count} ledger entries, so this customer cannot be deleted. "
                        "Delete those entries first if you really want to remove the customer."
                    )
                waiting = self.conn.execute(
                    "SELECT COUNT(*) FROM pending_entries WHERE customer_id = ?", (customer_id,)
                ).fetchone()[0]
                if waiting:
                    raise ValidationError(
                        f"{customer.name} has {waiting} Daily Entry row(s) waiting to be saved, so this customer "
                        "cannot be deleted. Delete those rows first."
                    )
                self.conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
                self._audit("CUSTOMER_DELETE", "customer", customer_id, customer_id,
                            f"Deleted customer '{customer.name}'")
        except sqlite3.IntegrityError:
            raise ValidationError(f"{customer.name} still has ledger entries and cannot be deleted") from None

    def list_customers(self, search: str = "") -> list[CustomerSummary]:
        rows = self.conn.execute(
            """SELECT c.id, c.name, c.mobile, c.address, c.notes, c.created_at, c.updated_at,
                      c.credit_limit_paise,
                      COALESCE(SUM(CASE WHEN t.txn_type = 'UDHAAR' THEN t.amount_paise END), 0) AS total_udhaar,
                      COALESCE(SUM(CASE WHEN t.txn_type = 'JAMA'   THEN t.amount_paise END), 0) AS total_jama,
                      COUNT(t.id) AS txn_count,
                      MAX(t.txn_date) AS last_txn_date
                 FROM customers c LEFT JOIN transactions t ON t.customer_id = c.id
                GROUP BY c.id"""
        ).fetchall()
        needle = " ".join(search.split()).casefold()
        # Match on phone digits only when what was typed looks like a phone number; otherwise 'Shop 2' would
        # match every customer whose number happens to contain a 2.
        looks_like_phone = bool(re.fullmatch(r"[0-9+\-() ]+", search.strip()))
        needle_digits = _digits(search) if looks_like_phone else ""
        result = []
        for r in rows:
            c = _row_to_customer(r)
            if needle:
                name_hit = needle in c.name.casefold()
                phone_hit = bool(needle_digits) and needle_digits in _digits(c.mobile)
                if not (name_hit or phone_hit):
                    continue
            result.append(CustomerSummary(c, r["total_udhaar"], r["total_jama"], r["txn_count"], r["last_txn_date"]))
        result.sort(key=lambda s: s.customer.name.casefold())
        return result

    def customer_balance(self, customer_id: int) -> int:
        return int(
            self.conn.execute(
                f"SELECT COALESCE(SUM({_SIGNED_AMOUNT}), 0) FROM transactions WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()[0]
        )

    # ---- transactions -----------------------------------------------------
    def _prepare_entry(self, txn_type, txn_date, item, amount, quantity, rate, due_date=None) -> dict:
        t = str(txn_type or "").strip().upper()
        if t not in TXN_TYPES:
            raise ValidationError(f"Transaction type must be {UDHAAR_LABEL} or {JAMA_LABEL}")
        try:
            date_iso = dates.parse_date(txn_date, today=self.today())
        except dates.DateError as exc:
            raise ValidationError(str(exc)) from None
        due_iso = None
        if not _is_blank(due_date):
            if t != UDHAAR:
                raise ValidationError(f"A due date can only be set on a '{UDHAAR_LABEL}' entry")
            try:
                due_iso = dates.parse_date(due_date, allow_future=True)
            except dates.DateError as exc:
                raise ValidationError(f"Due date: {exc}") from None
            if due_iso < date_iso:
                raise ValidationError("Due date cannot be before the transaction date")
        item = _clean_single_line(item, "Item / description", MAX_ITEM)
        try:
            qty = None if _is_blank(quantity) else money.parse_quantity(quantity)
            rate_paise = None if _is_blank(rate) else money.rupees_to_paise(rate, "Rate")
            if qty is not None and rate_paise is not None:
                computed = money.compute_amount_paise(qty, rate_paise)
                if not _is_blank(amount) and money.rupees_to_paise(amount) != computed:
                    raise ValidationError("Amount does not match Quantity x Rate")
                amount_paise = computed
            elif _is_blank(amount):
                raise ValidationError("Amount is required (or enter both Quantity and Rate)")
            else:
                amount_paise = money.rupees_to_paise(amount)
        except money.MoneyError as exc:
            raise ValidationError(str(exc)) from None
        return {
            "txn_type": t,
            "txn_date": date_iso,
            "item": item,
            "quantity": None if qty is None else money.quantity_to_text(qty),
            "rate_paise": rate_paise,
            "amount_paise": amount_paise,
            "due_date": due_iso,
        }

    def _timestamp(self, text: str) -> datetime:
        """Parse a stored ISO timestamp so it can be compared with ``self.now()`` (naive / aware safe)."""
        value = datetime.fromisoformat(text)
        now = self.now()
        if value.tzinfo is None and now.tzinfo is not None:
            return value.replace(tzinfo=now.tzinfo)
        if value.tzinfo is not None and now.tzinfo is None:
            return value.replace(tzinfo=None)
        return value

    def _recent_duplicate_exists(self, customer_id: int, f: dict, notes: str) -> bool:
        rows = self.conn.execute(
            "SELECT item, notes, created_at FROM transactions "
            "WHERE customer_id=? AND txn_date=? AND txn_type=? AND amount_paise=? ORDER BY id DESC LIMIT 5",
            (customer_id, f["txn_date"], f["txn_type"], f["amount_paise"]),
        ).fetchall()
        now = self.now()
        for r in rows:
            if r["item"].casefold() != f["item"].casefold() or r["notes"] != notes:
                continue
            try:
                created = self._timestamp(r["created_at"])
            except ValueError:
                continue
            if abs((now - created).total_seconds()) <= DUPLICATE_WINDOW_SECONDS:
                return True
        return False

    def get_transaction(self, txn_id: int) -> Transaction:
        row = self.conn.execute(f"SELECT {_TXN_COLUMNS} FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if row is None:
            raise NotFoundError("Entry not found")
        return _row_to_txn(row)

    def add_transaction(
        self, customer_id: int, txn_type, txn_date, item, amount=None, quantity=None, rate=None,
        notes="", allow_duplicate: bool = False, due_date=None, allow_over_limit: bool = False,
    ) -> Transaction:
        customer = self.get_customer(customer_id)
        f = self._prepare_entry(txn_type, txn_date, item, amount, quantity, rate, due_date)
        notes = _clean_multiline(notes, "Notes")
        with dbmod.transaction(self.conn):
            if not allow_duplicate and self._recent_duplicate_exists(customer_id, f, notes):
                raise DuplicateEntryError(
                    "An identical entry was saved a moment ago. Save it again only if it is a genuine second entry."
                )
            if f["txn_type"] == UDHAAR and customer.credit_limit_paise > 0 and not allow_over_limit:
                new_balance = self.customer_balance(customer_id) + f["amount_paise"]
                if new_balance > customer.credit_limit_paise:
                    raise CreditLimitError(
                        f"This {UDHAAR_WORD} entry takes {customer.name}'s balance to {money.format_inr(new_balance)}, above the "
                        f"credit limit of {money.format_inr(customer.credit_limit_paise)}.",
                        customer.credit_limit_paise, new_balance,
                    )
            now = self._now_iso()
            cur = self.conn.execute(
                "INSERT INTO transactions(customer_id, txn_date, item, quantity, rate_paise, amount_paise, "
                "txn_type, notes, created_at, updated_at, due_date) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (customer_id, f["txn_date"], f["item"], f["quantity"], f["rate_paise"], f["amount_paise"],
                 f["txn_type"], notes, now, now, f["due_date"]),
            )
            txn = self.get_transaction(cur.lastrowid)
            details = {"over_credit_limit": True} if (f["txn_type"] == UDHAAR and customer.credit_limit_paise > 0
                                                     and allow_over_limit
                                                     and self.customer_balance(customer_id) > customer.credit_limit_paise) else None
            self._audit("ENTRY_ADD", "transaction", txn.id, customer_id, self._txn_summary(txn, customer.name), details)
        return txn

    def update_transaction(
        self, txn_id: int, txn_type, txn_date, item, amount=None, quantity=None, rate=None, notes="",
        due_date=None, allow_over_limit: bool = False,
    ) -> Transaction:
        old = self.get_transaction(txn_id)
        customer = self.get_customer(old.customer_id)
        f = self._prepare_entry(txn_type, txn_date, item, amount, quantity, rate, due_date)
        notes = _clean_multiline(notes, "Notes")
        new_values = {
            "txn_date": f["txn_date"], "item": f["item"], "quantity": f["quantity"], "rate_paise": f["rate_paise"],
            "amount_paise": f["amount_paise"], "txn_type": f["txn_type"], "notes": notes, "due_date": f["due_date"],
        }
        changes = {k: [getattr(old, k), v] for k, v in new_values.items() if getattr(old, k) != v}
        with dbmod.transaction(self.conn):
            if customer.credit_limit_paise > 0 and not allow_over_limit:
                # Balance as it would be after this edit. Only an edit that RAISES the balance can be blocked, so
                # fixing a note or date on a customer who is already over the limit still works.
                current = self.customer_balance(old.customer_id)
                signed = lambda typ, amt: amt if typ == UDHAAR else -amt  # noqa: E731
                new_balance = current - signed(old.txn_type, old.amount_paise) + signed(f["txn_type"], f["amount_paise"])
                if new_balance > customer.credit_limit_paise and new_balance > current:
                    raise CreditLimitError(
                        f"This change takes {customer.name}'s balance to {money.format_inr(new_balance)}, above the "
                        f"credit limit of {money.format_inr(customer.credit_limit_paise)}.",
                        customer.credit_limit_paise, new_balance,
                    )
            # created_at is deliberately left untouched: it is the audit timestamp.
            self.conn.execute(
                "UPDATE transactions SET txn_date=?, item=?, quantity=?, rate_paise=?, amount_paise=?, "
                "txn_type=?, notes=?, due_date=?, updated_at=? WHERE id=?",
                (f["txn_date"], f["item"], f["quantity"], f["rate_paise"], f["amount_paise"],
                 f["txn_type"], notes, f["due_date"], self._now_iso(), txn_id),
            )
            if changes:
                if customer.credit_limit_paise > 0 and self.customer_balance(old.customer_id) > customer.credit_limit_paise:
                    changes["over_credit_limit"] = True
                self._audit("ENTRY_EDIT", "transaction", txn_id, old.customer_id,
                            "Edited entry: " + self._txn_summary(old, customer.name), changes)
        return self.get_transaction(txn_id)

    def delete_transaction(self, txn_id: int) -> None:
        old = self.get_transaction(txn_id)
        customer = self.get_customer(old.customer_id)
        with dbmod.transaction(self.conn):
            self.conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
            self._audit("ENTRY_DELETE", "transaction", txn_id, old.customer_id,
                        "Deleted entry: " + self._txn_summary(old, customer.name),
                        {k: getattr(old, k) for k in ("txn_date", "item", "quantity", "rate_paise", "amount_paise",
                                                      "txn_type", "notes", "due_date", "created_at")})

    # ---- daily entry: rows that wait 24 hours, then post to the ledger ----------
    def _pending_udhaar_total(self, customer_id: int) -> int:
        return int(self.conn.execute(
            "SELECT COALESCE(SUM(amount_paise), 0) FROM pending_entries WHERE customer_id = ? AND txn_type = 'UDHAAR'",
            (customer_id,),
        ).fetchone()[0])

    def _pending_summary(self, p: PendingEntry) -> str:
        return (f"{type_label(p.txn_type)} {money.format_inr(p.amount_paise)} - {p.item} "
                f"(dated {dates.format_date(p.txn_date)}) - {p.customer_name}")

    def add_pending_entry(
        self, customer_id: int, txn_type, txn_date, item, amount=None, quantity=None, rate=None,
        notes="", due_date=None, allow_duplicate: bool = False, allow_over_limit: bool = False,
    ) -> PendingEntry:
        """Put a Daily Entry row on hold. It is saved into the ledger automatically after PENDING_HOURS hours,
        unless it is deleted before that."""
        customer = self.get_customer(customer_id)
        f = self._prepare_entry(txn_type, txn_date, item, amount, quantity, rate, due_date)
        notes = _clean_multiline(notes, "Notes")
        now = self.now()
        with dbmod.transaction(self.conn):
            if not allow_duplicate:
                rows = self.conn.execute(
                    "SELECT item, notes, created_at FROM pending_entries WHERE customer_id=? AND txn_date=? "
                    "AND txn_type=? AND amount_paise=? ORDER BY id DESC LIMIT 5",
                    (customer_id, f["txn_date"], f["txn_type"], f["amount_paise"]),
                ).fetchall()
                for r in rows:
                    if r["item"].casefold() != f["item"].casefold() or r["notes"] != notes:
                        continue
                    try:
                        created = self._timestamp(r["created_at"])
                    except ValueError:
                        continue
                    if abs((now - created).total_seconds()) <= DUPLICATE_WINDOW_SECONDS:
                        raise DuplicateEntryError(
                            "An identical row was added a moment ago. Add it again only if it is a genuine second entry."
                        )
            if f["txn_type"] == UDHAAR and customer.credit_limit_paise > 0 and not allow_over_limit:
                new_balance = (self.customer_balance(customer_id) + self._pending_udhaar_total(customer_id)
                               + f["amount_paise"])
                if new_balance > customer.credit_limit_paise:
                    raise CreditLimitError(
                        f"This {UDHAAR_WORD} entry (with the entries already waiting) takes {customer.name}'s balance to "
                        f"{money.format_inr(new_balance)}, above the credit limit of "
                        f"{money.format_inr(customer.credit_limit_paise)}.",
                        customer.credit_limit_paise, new_balance,
                    )
            cur = self.conn.execute(
                "INSERT INTO pending_entries(customer_id, txn_date, item, quantity, rate_paise, amount_paise, "
                "txn_type, notes, due_date, created_at, post_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (customer_id, f["txn_date"], f["item"], f["quantity"], f["rate_paise"], f["amount_paise"],
                 f["txn_type"], notes, f["due_date"], now.isoformat(timespec="seconds"),
                 (now + timedelta(hours=PENDING_HOURS)).isoformat(timespec="seconds")),
            )
            pending = self.get_pending_entry(cur.lastrowid)
            self._audit("DAILY_ADD", "pending_entry", pending.id, customer_id,
                        "Added to Daily Entry (saves automatically in 24 hours): " + self._pending_summary(pending))
        return pending

    def get_pending_entry(self, pending_id: int) -> PendingEntry:
        row = self.conn.execute(_PENDING_SELECT + " WHERE p.id = ?", (pending_id,)).fetchone()
        if row is None:
            raise NotFoundError("Daily Entry row not found (it may already have been saved or deleted)")
        return _row_to_pending(row)

    def list_pending(self, customer_id: Optional[int] = None) -> list[PendingEntry]:
        sql = _PENDING_SELECT
        params: list = []
        if customer_id is not None:
            sql += " WHERE p.customer_id = ?"
            params.append(customer_id)
        rows = [_row_to_pending(r) for r in self.conn.execute(sql + " ORDER BY p.id", params).fetchall()]
        rows.sort(key=lambda p: (self._timestamp(p.post_at), p.id))
        return rows

    def seconds_until_post(self, p: PendingEntry) -> int:
        """Seconds left before ``p`` is saved automatically (0 = due now)."""
        return max(0, int((self._timestamp(p.post_at) - self.now()).total_seconds()))

    def delete_pending(self, pending_id: int) -> None:
        """Remove a waiting row, e.g. the customer brought the goods back. Nothing reaches the ledger."""
        p = self.get_pending_entry(pending_id)
        with dbmod.transaction(self.conn):
            self.conn.execute("DELETE FROM pending_entries WHERE id = ?", (pending_id,))
            self._audit("DAILY_DELETE", "pending_entry", pending_id, p.customer_id,
                        "Deleted from Daily Entry before it was saved: " + self._pending_summary(p),
                        {"queued_at": p.created_at})

    def _post_pending_locked(self, p: PendingEntry, how: str) -> Transaction:
        """Move one waiting row into the ledger. Must run inside a DB transaction."""
        p = self.get_pending_entry(p.id)  # re-read under the lock: another window may have saved / deleted it already
        now = self._now_iso()
        cur = self.conn.execute(
            "INSERT INTO transactions(customer_id, txn_date, item, quantity, rate_paise, amount_paise, "
            "txn_type, notes, created_at, updated_at, due_date) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (p.customer_id, p.txn_date, p.item, p.quantity, p.rate_paise, p.amount_paise, p.txn_type, p.notes,
             now, now, p.due_date),
        )
        self.conn.execute("DELETE FROM pending_entries WHERE id = ?", (p.id,))
        txn = self.get_transaction(cur.lastrowid)
        details: dict = {"from_daily_entry": how, "queued_at": p.created_at}
        limit = self.get_customer(p.customer_id).credit_limit_paise
        if p.txn_type == UDHAAR and limit > 0 and self.customer_balance(p.customer_id) > limit:
            details["over_credit_limit"] = True
        self._audit("ENTRY_ADD", "transaction", txn.id, p.customer_id,
                    self._txn_summary(txn, p.customer_name) + f" [saved from Daily Entry: {how}]", details)
        return txn

    def post_pending(self, pending_id: int) -> Transaction:
        """'Save now': put a waiting row into the ledger immediately instead of waiting for the 24 hours."""
        with dbmod.transaction(self.conn):
            p = self.get_pending_entry(pending_id)
            return self._post_pending_locked(p, "saved early by the user")

    def post_due_pending(self) -> list[Transaction]:
        """Save every waiting row whose 24 hours are over. Safe to call as often as you like."""
        now = self.now()
        posted: list[Transaction] = []
        for p in self.list_pending():
            if self._timestamp(p.post_at) > now:
                break  # list is sorted by post time; the rest are not due yet
            try:
                with dbmod.transaction(self.conn):
                    posted.append(self._post_pending_locked(p, "automatically after 24 hours"))
            except NotFoundError:
                continue  # another window already saved or deleted this row
        return posted

    # ---- ledger -----------------------------------------------------------
    def _range_iso(self, date_from, date_to) -> tuple[Optional[str], Optional[str]]:
        try:
            f = None if _is_blank(date_from) else dates.parse_date(date_from, allow_future=True)
            t = None if _is_blank(date_to) else dates.parse_date(date_to, allow_future=True)
        except dates.DateError as exc:
            raise ValidationError(str(exc)) from None
        if f and t and f > t:
            raise ValidationError("'From' date cannot be after 'To' date")
        return f, t

    def ledger(self, customer_id: int, date_from=None, date_to=None, search: str = "") -> Ledger:
        customer = self.get_customer(customer_id)
        f, t = self._range_iso(date_from, date_to)

        opening = 0
        if f:
            opening = int(
                self.conn.execute(
                    f"SELECT COALESCE(SUM({_SIGNED_AMOUNT}), 0) FROM transactions "
                    "WHERE customer_id = ? AND txn_date < ?",
                    (customer_id, f),
                ).fetchone()[0]
            )

        sql = f"SELECT {_TXN_COLUMNS} FROM transactions WHERE customer_id = ?"
        params: list = [customer_id]
        if f:
            sql += " AND txn_date >= ?"
            params.append(f)
        if t:
            sql += " AND txn_date <= ?"
            params.append(t)
        sql += " ORDER BY txn_date, id"  # same-date entries keep the order they were entered

        needle = " ".join(search.split()).casefold()
        balance = opening
        rows: list[LedgerRow] = []
        total_u = total_j = 0
        in_period = 0
        for r in self.conn.execute(sql, params).fetchall():
            txn = _row_to_txn(r)
            in_period += 1
            udhaar = txn.amount_paise if txn.txn_type == UDHAAR else 0
            jama = txn.amount_paise if txn.txn_type == JAMA else 0
            balance += udhaar - jama  # running balance always reflects the true account
            if needle and needle not in txn.item.casefold():
                continue
            total_u += udhaar
            total_j += jama
            rows.append(LedgerRow(txn, udhaar, jama, balance))
        return Ledger(customer, f, t, search.strip(), opening, rows, total_u, total_j, balance, in_period)

    def register(self, date_from=None, date_to=None, search: str = "",
                 customer_ids: Optional[Sequence[int]] = None) -> Register:
        """Entries across customers (no running balance), for date/customer/item filtered reports."""
        f, t = self._range_iso(date_from, date_to)
        sql = ("SELECT t.id, t.customer_id, t.txn_date, t.item, t.quantity, t.rate_paise, t.amount_paise, "
               "t.txn_type, t.notes, t.created_at, t.updated_at, t.due_date, c.name AS customer_name "
               "FROM transactions t JOIN customers c ON c.id = t.customer_id WHERE 1=1")
        params: list = []
        ids = None if customer_ids is None else list(customer_ids)
        if ids is not None:
            if not ids:
                return Register(f, t, search.strip(), ids, [], 0, 0)
            sql += " AND t.customer_id IN (%s)" % ",".join("?" * len(ids))
            params.extend(ids)
        if f:
            sql += " AND t.txn_date >= ?"
            params.append(f)
        if t:
            sql += " AND t.txn_date <= ?"
            params.append(t)
        sql += " ORDER BY t.txn_date, c.name COLLATE NOCASE, t.id"
        needle = " ".join(search.split()).casefold()
        rows: list[RegisterRow] = []
        total_u = total_j = 0
        for r in self.conn.execute(sql, params).fetchall():
            txn = _row_to_txn(r)
            if needle and needle not in txn.item.casefold():
                continue
            rows.append(RegisterRow(txn, r["customer_name"]))
            if txn.txn_type == UDHAAR:
                total_u += txn.amount_paise
            else:
                total_j += txn.amount_paise
        return Register(f, t, search.strip(), ids, rows, total_u, total_j)

    # ---- dashboard --------------------------------------------------------
    def dashboard(self) -> DashboardSummary:
        today = self.today().isoformat()
        customers = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        r = self.conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN txn_type = 'UDHAAR' THEN amount_paise END), 0),
                      COALESCE(SUM(CASE WHEN txn_type = 'JAMA'   THEN amount_paise END), 0),
                      COALESCE(SUM(txn_date = ?), 0),
                      COALESCE(SUM(txn_date < ?), 0),
                      COUNT(*)
                 FROM transactions""",
            (today, today),
        ).fetchone()
        return DashboardSummary(customers, r[0], r[1], r[2], r[3], r[4])


    # ---- Daily Udhaar-Jama summary ----------------------------------------
    def daily_summary(self, day=None, basis: str = BASIS_TXN_DATE) -> DailySummary:
        """Live figures for one day, computed from the saved transactions only (every call re-reads the database,
        so adding, editing or deleting an entry is reflected the next time this runs; each row is counted once)."""
        if day is None:
            day = self.today()
        iso = day.isoformat() if isinstance(day, date) else str(day)
        try:
            datetime.strptime(iso, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError("Choose a valid date") from exc
        if basis not in (BASIS_TXN_DATE, BASIS_ENTERED):
            raise ValidationError("Unknown summary basis")
        column = "t.txn_date" if basis == BASIS_TXN_DATE else "substr(t.created_at, 1, 10)"
        cur = self.conn.execute(
            f"""SELECT t.id, t.customer_id, c.name, t.txn_type, t.amount_paise, t.txn_date, t.created_at,
                       t.item, t.notes
                  FROM transactions t JOIN customers c ON c.id = t.customer_id
                 WHERE {column} = ?
                 ORDER BY t.created_at, t.id""", (iso,))
        rows = [DailyRow(r[0], r[1], r[2], r[3], r[4], r[5], (r[6] or "")[11:19], (r[6] or "")[:10], r[7], r[8] or "")
                for r in cur.fetchall()]
        jama = sum(r.amount_paise for r in rows if r.txn_type == JAMA)
        udhaar = sum(r.amount_paise for r in rows if r.txn_type == UDHAAR)
        return DailySummary(iso, basis, jama, udhaar, len(rows), len({r.customer_id for r in rows}), rows)

    # ---- backup / restore -------------------------------------------------
    def create_backup(self, dest_path) -> Path:
        dest = Path(dest_path)
        if dest.resolve() == self.db_path.resolve():
            raise backup_mod.BackupError("Choose a different file than the live database")
        return backup_mod.create_backup(self.conn, dest)

    @property
    def backups(self) -> BackupManager:
        """Automatic / manual backups to Downloads/SJB KHATA BACKUP 24 (see autobackup.py)."""
        if self._backups is None:
            self._backups = BackupManager(self.db_path, clock=self._clock)
        return self._backups

    def auto_backup_if_due(self, force: bool = False) -> Optional[Path]:
        """Automatic backup when enabled and 24 h old (or ``force``: make one now). Never deletes older backups.
        Returns the new file, or None when nothing was due. Raises BackupError when a backup fails."""
        if force:
            return self.backups.run_backup("manual", self.conn)
        return self.backups.run_if_due("auto", self.conn)

    def restore_backup(self, backup_path, safety_dir=None) -> Path:
        """Validate a backup, snapshot the current data, then replace it.

        Returns the path of the safety backup that was written first.
        """
        source = Path(backup_path)
        if source.resolve() == self.db_path.resolve():
            raise backup_mod.BackupError("That is the live database, not a backup file")
        backup_mod.validate_backup(source)

        safety_dir = Path(safety_dir) if safety_dir else self.db_path.parent / "safety_backups"
        safety_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.now().strftime("%Y%m%d-%H%M%S")
        safety = safety_dir / f"before-restore-{stamp}.db"
        n = 1
        while safety.exists():
            n += 1
            safety = safety_dir / f"before-restore-{stamp}-{n}.db"
        backup_mod.create_backup(self.conn, safety)

        staged = self.db_path.with_name(self.db_path.name + ".restore-tmp")
        try:
            shutil.copyfile(source, staged)
            dbmod.open_database(staged, premigration_backup=False).close()  # upgrade an older backup
            backup_mod.validate_backup(staged)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

        self.conn.close()
        try:
            for suffix in ("-wal", "-shm", "-journal"):
                self.db_path.with_name(self.db_path.name + suffix).unlink(missing_ok=True)
            staged.replace(self.db_path)
        except OSError as exc:
            staged.unlink(missing_ok=True)
            self.conn = dbmod.open_database(self.db_path)
            raise backup_mod.BackupError(f"Could not replace the database: {exc}") from exc
        finally:
            if self.conn is None or not _is_open(self.conn):
                self.conn = dbmod.open_database(self.db_path)
        with dbmod.transaction(self.conn):
            self._audit("RESTORE", "database", None, None, f"Restored data from backup '{source.name}'",
                        {"safety_backup": str(safety)})
        return safety


def _is_open(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False
