"""Money handling.

All money is stored and calculated as integer paise (1 rupee = 100 paise) so
that totals never suffer floating-point drift.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_PAISE = 100_000_000_000  # Rs 100 crore per single entry - a sanity cap
MAX_QUANTITY = Decimal("1000000000")

_TWO_PLACES = Decimal("0.01")
_THREE_PLACES = Decimal("0.001")


class MoneyError(ValueError):
    """Raised when an amount, rate or quantity cannot be accepted."""


_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", re.ASCII)
_GROUPED_RE = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d*)?", re.ASCII)
_MAX_TEXT_LEN = 40
_MAX_DIGITS_BEFORE_POINT = 18  # far above MAX_PAISE; keeps every Decimal operation inside default precision


def _to_decimal(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise MoneyError(f"{label} is not a valid number")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    else:
        text = str(value).strip()
        for junk in ("₹", "Rs.", "Rs", "INR"):
            text = text.replace(junk, "")
        text = text.strip()
        if not text:
            raise MoneyError(f"{label} is empty")
        if "," in text:
            # Commas are accepted only as proper thousands separators (1,234,567 or 12,34,567), so a typo like
            # "1,2,3" is rejected instead of silently becoming 123.
            if not _GROUPED_RE.fullmatch(text):
                raise MoneyError(f"{label} '{str(value)[:_MAX_TEXT_LEN]}' is not a valid number")
            text = text.replace(",", "")
        if len(text) > _MAX_TEXT_LEN or not _NUMBER_RE.fullmatch(text):
            raise MoneyError(f"{label} '{str(value)[:_MAX_TEXT_LEN]}' is not a valid number")
        d = Decimal(text)
    if not d.is_finite():
        raise MoneyError(f"{label} is not a valid number")
    if d != 0 and d.adjusted() > _MAX_DIGITS_BEFORE_POINT:
        raise MoneyError(f"{label} is too large")
    return d


def rupees_to_paise(value, label: str = "Amount") -> int:
    """Parse a rupee value (max two decimals) into integer paise."""
    d = _to_decimal(value, label)
    try:
        exact = d == d.quantize(_TWO_PLACES)
    except InvalidOperation:
        raise MoneyError(f"{label} is too large") from None
    if not exact:
        raise MoneyError(f"{label} can have at most 2 decimal places")
    paise = int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))
    if paise <= 0:
        raise MoneyError(f"{label} must be greater than zero")
    if paise > MAX_PAISE:
        raise MoneyError(f"{label} is too large")
    return paise


def parse_quantity(value, label: str = "Quantity") -> Decimal:
    """Parse a quantity (max three decimals, e.g. 0.125 kg)."""
    d = _to_decimal(value, label)
    try:
        exact = d == d.quantize(_THREE_PLACES)
    except InvalidOperation:
        raise MoneyError(f"{label} is too large") from None
    if not exact:
        raise MoneyError(f"{label} can have at most 3 decimal places")
    if d <= 0:
        raise MoneyError(f"{label} must be greater than zero")
    if d > MAX_QUANTITY:
        raise MoneyError(f"{label} is too large")
    return d


def quantity_to_text(qty: Decimal) -> str:
    """Canonical text form used for storage/display ('3', '2.5', '100')."""
    return format(qty.normalize(), "f")


def compute_amount_paise(qty: Decimal, rate_paise: int) -> int:
    """Quantity x Rate, rounded half-up to the nearest paisa."""
    try:
        paise = int((qty * rate_paise).to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise MoneyError("Quantity x Rate is too large") from None
    if paise <= 0:
        raise MoneyError("Quantity x Rate must be at least 1 paisa")
    if paise > MAX_PAISE:
        raise MoneyError("Quantity x Rate is too large")
    return paise


def paise_to_decimal(paise: int) -> Decimal:
    return (Decimal(paise) / Decimal(100)).quantize(_TWO_PLACES)


def plain_amount(paise: int) -> str:
    """'600.00' - for editing fields (no grouping, no symbol)."""
    return f"{paise_to_decimal(paise):.2f}"


def format_inr(paise: int, symbol: bool = True) -> str:
    """Indian digit grouping: 1234567 paise-rupees -> 12,34,567.00."""
    negative = paise < 0
    rupees, ps = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    text = f"{digits}.{ps:02d}"
    if symbol:
        text = "₹" + text
    return "-" + text if negative else text
