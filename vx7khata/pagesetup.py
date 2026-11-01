"""Print setup: paper size, orientation and WHAT to print. Remembered between prints. No Qt here (unit-testable)."""
from __future__ import annotations

from dataclasses import dataclass

from .service import KhataService, ValidationError

CUSTOM = "Custom"            # value of ``size`` when the person typed their own width x height
DEFAULT_SIZE = "A4"
PORTRAIT, LANDSCAPE = "portrait", "landscape"
MIN_MM, MAX_MM = 50.0, 1000.0

# Ledger table columns, in print order, then the extra blocks that can be switched on / off.
COLUMN_LABELS = {
    "date": "Date", "item": "Item / Description", "qty": "Quantity", "rate": "Rate",
    "debit": "Debit (Dr)", "credit": "Credit (Cr)", "balance": "Balance", "notes": "Notes",
}
SECTION_LABELS = {
    "shop": "Shop name and details (top)",
    "contact": "Customer mobile and address",
    "summary": "Totals box below the table",
    "printed_on": "'Printed on' date (bottom)",
}
COLUMNS = tuple(COLUMN_LABELS)
SECTIONS = tuple(SECTION_LABELS)
ALL_ITEMS = COLUMNS + SECTIONS
DEFAULT_ITEMS = tuple(k for k in ALL_ITEMS if k != "balance")   # balance is off unless the person ticks it

_SETTING_ITEMS = "print_items"


@dataclass(frozen=True)
class PageSetup:
    size: str = DEFAULT_SIZE          # a QPageSize.PageSizeId member name ("A4", "A5", "Letter" ...) or CUSTOM
    orientation: str = PORTRAIT
    custom_w_mm: float = 210.0
    custom_h_mm: float = 297.0
    items: tuple = DEFAULT_ITEMS      # keys of ALL_ITEMS that are printed

    @property
    def is_custom(self) -> bool:
        return self.size == CUSTOM

    @property
    def show_balance(self) -> bool:
        return "balance" in self.items


def validate(setup: PageSetup) -> PageSetup:
    """Return ``setup`` or raise ValidationError."""
    if setup.orientation not in (PORTRAIT, LANDSCAPE):
        raise ValidationError("Orientation must be Portrait or Landscape")
    if setup.is_custom:
        for label, value in (("Width", setup.custom_w_mm), ("Height", setup.custom_h_mm)):
            if not MIN_MM <= value <= MAX_MM:
                raise ValidationError(f"{label} must be between {MIN_MM:g} and {MAX_MM:g} mm")
    unknown = [k for k in setup.items if k not in ALL_ITEMS]
    if unknown:
        raise ValidationError("Unknown print item: " + ", ".join(unknown))
    if not any(k in COLUMNS for k in setup.items):
        raise ValidationError("Select at least one column to print")
    return setup


def load(svc: KhataService) -> PageSetup:
    """Last used setup; falls back to A4 portrait with the default items when nothing usable is saved."""
    try:
        size = svc.get_setting("print_page_size", DEFAULT_SIZE) or DEFAULT_SIZE
        orientation = svc.get_setting("print_orientation", PORTRAIT)
        w = float(svc.get_setting("print_custom_w", "210"))
        h = float(svc.get_setting("print_custom_h", "297"))
        raw = svc.get_setting(_SETTING_ITEMS, "")
        if raw:
            chosen = set(raw.split(","))
            items = tuple(k for k in ALL_ITEMS if k in chosen)
        elif svc.get_setting("print_show_balance", "0") == "1":   # setting written by the previous version
            items = ALL_ITEMS
        else:
            items = DEFAULT_ITEMS
        return validate(PageSetup(size, orientation, w, h, items))
    except (ValueError, ValidationError):
        return PageSetup()


def save(svc: KhataService, setup: PageSetup) -> None:
    validate(setup)
    values = {
        "print_page_size": setup.size, "print_orientation": setup.orientation,
        "print_custom_w": f"{setup.custom_w_mm:g}", "print_custom_h": f"{setup.custom_h_mm:g}",
        _SETTING_ITEMS: ",".join(k for k in ALL_ITEMS if k in setup.items),
    }
    for key, value in values.items():
        svc.set_setting(key, value)
