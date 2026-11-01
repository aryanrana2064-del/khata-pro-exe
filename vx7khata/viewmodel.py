"""Presentation logic with no Qt dependency, so it can be unit-tested anywhere."""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Optional

from . import dates, money
from .service import AuditEntry, CustomerSummary

WARN_FRACTION = 0.8

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def compact_inr(paise: int) -> str:
    """Short Indian-style label for chart axes: 1,50,000 -> '1.5L', 2,500 -> '2.5K', 3,00,00,000 -> '3Cr'."""
    sign = "-" if paise < 0 else ""
    rupees = abs(paise) / 100
    units = ((1e7, "Cr"), (1e5, "L"), (1e3, "K"))
    for i, (limit, suffix) in enumerate(units):
        if rupees >= limit:
            value = round(rupees / limit, 1)
            if value >= 100 and i > 0:  # 99,950 would print as '100K': move up to '1L' instead
                limit, suffix = units[i - 1]
                value = round(rupees / limit, 1)
            text = f"{value:.1f}"
            return f"{sign}{text[:-2] if text.endswith('.0') else text}{suffix}"
    return f"{sign}{int(round(rupees))}"


def nice_axis(max_paise: int, ticks: int = 4) -> list[int]:
    """Tick values in paise from 0 up to a 'nice' maximum >= max_paise (always ``ticks`` + 1 values)."""
    if ticks < 1:
        raise ValueError("ticks must be >= 1")
    max_rupees = max(0, max_paise) / 100
    if max_rupees <= 0:
        step = 1000.0
    else:
        raw = max(max_rupees / ticks, 1.0)  # never finer than Rs 1, so ticks stay distinct whole paise
        magnitude = 10 ** math.floor(math.log10(raw))
        step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw - 1e-9)
    return [int(round(step * i * 100)) for i in range(ticks + 1)]


def month_label(month: str) -> str:
    """'2026-10' -> 'Oct 26'."""
    year, mon = month.split("-")
    return f"{_MONTHS[int(mon) - 1]} {year[2:]}"


def credit_state(summary: CustomerSummary) -> tuple[str, float]:
    """('none'|'ok'|'warn'|'over', fraction of limit used). 'warn' starts at 80 %."""
    limit = summary.customer.credit_limit_paise
    if limit <= 0:
        return "none", 0.0
    fraction = max(0.0, summary.balance / limit)
    if fraction > 1.0:
        return "over", fraction
    if fraction >= WARN_FRACTION:
        return "warn", fraction
    return "ok", fraction


def credit_text(summary: CustomerSummary) -> str:
    state, fraction = credit_state(summary)
    if state == "none":
        return "No limit"
    return f"{money.format_inr(summary.customer.credit_limit_paise)}  ({round(fraction * 100)}% used)"


def due_text(due_iso: Optional[str], today: date) -> str:
    if not due_iso:
        return ""
    delta = (dates.to_date(due_iso) - today).days
    if delta < 0:
        return f"Overdue {-delta} day{'s' if -delta != 1 else ''}"
    if delta == 0:
        return "Due today"
    return f"Due in {delta} day{'s' if delta != 1 else ''}"


_FIELD_LABELS = {
    "txn_date": "Date", "item": "Item", "quantity": "Quantity", "rate_paise": "Rate", "amount_paise": "Amount",
    "txn_type": "Type", "notes": "Notes", "due_date": "Due date", "name": "Name", "mobile": "Mobile",
    "address": "Address", "credit_limit_paise": "Credit limit", "created_at": "Originally entered",
    "over_credit_limit": "Saved above credit limit", "safety_backup": "Safety copy",
}


def _value_text(key: str, value) -> str:
    if value is None or value == "":
        return "(empty)"
    if key.endswith("_paise") and isinstance(value, int):
        return "none" if (key == "credit_limit_paise" and value == 0) else money.format_inr(value)
    if key in ("txn_date", "due_date") and isinstance(value, str) and len(value) == 10:
        try:
            return dates.format_date(value)
        except (ValueError, IndexError):
            return value
    if key == "created_at" and isinstance(value, str):
        try:
            return datetime.fromisoformat(value).strftime("%d-%m-%Y %H:%M")
        except ValueError:
            return value
    return str(value)


def audit_details_lines(entry: AuditEntry) -> list[str]:
    """Human-readable lines for the details of an audit entry ('Amount: ₹500.00 -> ₹600.00')."""
    if not entry.details:
        return []
    try:
        data = json.loads(entry.details)
    except ValueError:
        return [entry.details]
    if not isinstance(data, dict):
        return [str(data)]
    lines = []
    for key in sorted(data):
        label = _FIELD_LABELS.get(key, key)
        value = data[key]
        if isinstance(value, list) and len(value) == 2:
            lines.append(f"{label}: {_value_text(key, value[0])}  →  {_value_text(key, value[1])}")
        else:
            lines.append(f"{label}: {_value_text(key, value)}")
    return lines


ACTION_LABELS = {
    "CUSTOMER_ADD": "Customer added", "CUSTOMER_EDIT": "Customer edited", "CUSTOMER_DELETE": "Customer deleted",
    "ENTRY_ADD": "Entry added", "ENTRY_EDIT": "Entry edited", "ENTRY_DELETE": "Entry deleted",
    "RESTORE": "Backup restored",
    "DAILY_ADD": "Daily entry added", "DAILY_DELETE": "Daily entry deleted",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace("_", " ").title())


def timestamp_text(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return iso


def time_left_text(seconds: int) -> str:
    """'5h 20m', '12 min', 'saving now' - how long a Daily Entry row still waits before it is saved."""
    seconds = int(seconds)
    if seconds <= 0:
        return "saving now"
    minutes = max(1, -(-seconds // 60))  # round up so it never shows 0 min while still waiting
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes} min"
