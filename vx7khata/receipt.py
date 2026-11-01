"""Thermal slip receipt (58 / 80 mm) for ONE entry - a separate printer choice from the A4 statements.

Settings live in the settings table. The HTML builder here is Qt-free (unit-testable); the actual printing
(QPrinter with a custom page: width = roll width, height = measured content height) lives in the main window.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from . import dates, money
from .labels import type_label
from .service import KhataService

SETTING_PRINTER = "receipt_printer"            # printer name; "" = ask with the print dialog every time
SETTING_WIDTH = "receipt_width_mm"             # "58" or "80"
SETTING_SHOW_BALANCE = "receipt_show_balance"  # "1" / "0"
WIDTHS = (58, 80)
DEFAULT_WIDTH_MM = 80


@dataclass(frozen=True)
class ReceiptSetup:
    printer: str = ""
    width_mm: int = DEFAULT_WIDTH_MM
    show_balance: bool = True


def load(service: KhataService) -> ReceiptSetup:
    printer = service.get_setting(SETTING_PRINTER, "")
    try:
        width = int(service.get_setting(SETTING_WIDTH, str(DEFAULT_WIDTH_MM)))
    except ValueError:
        width = DEFAULT_WIDTH_MM
    if width not in WIDTHS:
        width = DEFAULT_WIDTH_MM
    return ReceiptSetup(printer, width, service.get_setting(SETTING_SHOW_BALANCE, "1") != "0")


def save(service: KhataService, setup: ReceiptSetup) -> None:
    service.set_setting(SETTING_PRINTER, (setup.printer or "").strip())
    service.set_setting(SETTING_WIDTH, str(setup.width_mm if setup.width_mm in WIDTHS else DEFAULT_WIDTH_MM))
    service.set_setting(SETTING_SHOW_BALANCE, "1" if setup.show_balance else "0")


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True).replace("\n", "<br>")


def _row(left: str, right: str = "", bold: bool = False) -> str:
    weight = "font-weight:bold;" if bold else ""
    return (f'<tr><td style="padding:0;{weight}">{left}</td>'
            f'<td style="padding:0;text-align:right;{weight}">{right}</td></tr>')


def build_receipt_html(shop_name: str, shop_details: str, customer, txn, *,
                       width_mm: int = DEFAULT_WIDTH_MM, balance_paise: int | None = None,
                       print_time: str = "") -> str:
    """One thermal slip as HTML. ``customer`` / ``txn`` need the usual attributes (name, mobile, item, ...).

    ``balance_paise`` is the customer's balance AFTER this entry: positive = the customer still owes,
    negative = advance with you. None omits the balance line.
    """
    if width_mm not in WIDTHS:
        width_mm = DEFAULT_WIDTH_MM
    font = 10 if width_mm == 58 else 11
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>',
        f"body {{ width:{width_mm - 4}mm; margin:0; padding:0; font-family:'Segoe UI',Arial,sans-serif;"
        f" font-size:{font}px; color:#000 }}",
        "table { width:100%; border-collapse:collapse }",
        ".c { text-align:center } .big { font-size:140%; font-weight:bold }",
        ".line { border-top:1px dashed #000; margin:4px 0 }",
        "</style></head><body>",
    ]
    if shop_name:
        parts.append(f'<div class="c" style="font-weight:bold;font-size:130%">{_esc(shop_name)}</div>')
    if shop_details:
        parts.append(f'<div class="c">{_esc(shop_details)}</div>')
    parts.append('<div class="line"></div>')

    parts.append("<table>")
    parts.append(_row("Customer", _esc(customer.name)))
    if getattr(customer, "mobile", ""):
        parts.append(_row("Mobile", _esc(customer.mobile)))
    parts.append(_row("Date", _esc(dates.format_date(txn.txn_date))))
    parts.append(_row("Receipt No.", _esc(txn.id)))
    parts.append(_row("Type", _esc(type_label(txn.txn_type))))
    parts.append("</table>")
    parts.append('<div class="line"></div>')

    parts.append("<table>")
    parts.append(_row(_esc(txn.item), bold=True))
    if txn.quantity and txn.rate_paise:
        parts.append(_row(f"{_esc(txn.quantity)} x {_esc(money.format_inr(txn.rate_paise, symbol=False))}",
                          _esc(money.format_inr(txn.amount_paise))))
    else:
        parts.append(_row("", _esc(money.format_inr(txn.amount_paise))))
    parts.append(_row('<span class="big">TOTAL</span>',
                      f'<span class="big">{_esc(money.format_inr(txn.amount_paise))}</span>'))
    if getattr(txn, "due_date", None):
        parts.append(_row("Promised by", _esc(dates.format_date(txn.due_date))))
    if balance_paise is not None:
        word = "to pay" if balance_paise > 0 else ("advance" if balance_paise < 0 else "settled")
        parts.append(_row(f"Balance ({word})", _esc(money.format_inr(balance_paise))))
    parts.append("</table>")

    if txn.notes:
        parts.append('<div class="line"></div>')
        parts.append(f"<div>{_esc(txn.notes)}</div>")
    parts.append('<div class="line"></div>')
    parts.append('<div class="c" style="font-weight:bold">Thank you!</div>')
    if print_time:
        parts.append(f'<div class="c">Printed {_esc(print_time)}</div>')
    parts.append("</body></html>")
    return "".join(parts)
