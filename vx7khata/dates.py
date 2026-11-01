"""Date helpers. The database stores ISO dates (YYYY-MM-DD); people see DD-MM-YYYY."""
from __future__ import annotations

import re
from datetime import date, datetime

MIN_YEAR = 1990


class DateError(ValueError):
    """Raised when a date cannot be accepted."""


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value, today: date | None = None, allow_future: bool = False) -> str:
    """Return an ISO date string.

    Accepts a ``date``/``datetime`` or text in DD-MM-YYYY (``/`` and ``.`` are
    accepted as separators). ISO YYYY-MM-DD text is accepted for internal use.
    """
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        text = str(value).strip()
        if not text:
            raise DateError("Date is empty")
        try:
            if _ISO_RE.match(text):
                d = datetime.strptime(text, "%Y-%m-%d").date()
            else:
                text = text.replace("/", "-").replace(".", "-")
                d = datetime.strptime(text, "%d-%m-%Y").date()
        except ValueError:
            raise DateError(f"'{value}' is not a valid date. Use DD-MM-YYYY.") from None
    if d.year < MIN_YEAR:
        raise DateError(f"Date must be {MIN_YEAR} or later")
    if not allow_future and today is not None and d > today:
        raise DateError("Date cannot be in the future")
    return d.isoformat()


def format_date(iso: str) -> str:
    """ISO -> DD-MM-YYYY."""
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


def to_date(iso: str) -> date:
    return datetime.strptime(iso, "%Y-%m-%d").date()
