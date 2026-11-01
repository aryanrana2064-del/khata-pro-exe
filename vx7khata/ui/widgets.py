"""Small reusable Qt widgets and helpers."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QCompleter, QFormLayout, QFrame,
    QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .. import dates, money
from ..labels import JAMA_LABEL, UDHAAR_LABEL
from ..service import KhataError
from . import icons, theme

MIN_DATE = QDate(dates.MIN_YEAR, 1, 1)
DISPLAY_FORMAT = "dd-MM-yyyy"


def danger_color() -> str:
    return theme.current()["danger"]


def success_color() -> str:
    return theme.current()["success"]


def balance_color(paise: int) -> Optional[str]:
    if paise > 0:
        return danger_color()  # customer owes
    if paise < 0:
        return success_color()  # advance / overpaid
    return None


def set_kind(widget: QWidget, kind: str) -> None:
    """Switch a widget's 'kind' style property (danger/success/warning/...) and re-apply the stylesheet."""
    widget.setProperty("kind", kind)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# --------------------------------------------------------------------------- glass panels
def glass_shadow(widget: QWidget) -> QGraphicsDropShadowEffect:
    """The soft shadow under a glass panel: style sheets cannot express one, so it is a graphics effect."""
    spec = theme.shadow_spec()
    colour = QColor(spec["color"])
    colour.setAlpha(int(spec["alpha"]))
    effect = QGraphicsDropShadowEffect(widget)
    effect.setColor(colour)
    effect.setBlurRadius(float(spec["blur"]))
    effect.setOffset(0, float(spec["offset_y"]))
    widget.setGraphicsEffect(effect)
    return effect


def refresh_shadows(root: QWidget) -> None:
    """Re-read the current palette's shadow for every panel under ``root`` (called when the theme changes)."""
    spec = theme.shadow_spec()
    colour = QColor(spec["color"])
    colour.setAlpha(int(spec["alpha"]))
    for effect in root.findChildren(QGraphicsDropShadowEffect):
        effect.setColor(colour)
        effect.setBlurRadius(float(spec["blur"]))
        effect.setOffset(0, float(spec["offset_y"]))


# --------------------------------------------------------------------------- drawn glyphs
GLYPH = "glyph"              # dynamic property: which shape from ui/icons.py to draw
GLYPH_COLOUR = "glyphColour"  # dynamic property: palette key of the stroke colour
GLYPH_SIZE = "glyphSize"
_motion_preference: Optional[bool] = None


def _paint_glyph(widget: QWidget) -> None:
    """Draw (or redraw) one glyph from the current palette. Widgets remember what they need."""
    name = widget.property(GLYPH)
    if not name:
        return
    pal = theme.current()
    size = int(widget.property(GLYPH_SIZE) or 22)
    colour = pal[str(widget.property(GLYPH_COLOUR) or "primary")]
    if isinstance(widget, QLabel):
        widget.setPixmap(icons.pixmap(str(name), colour, max(10, int(size * 0.6))))
        widget.setFixedSize(size, size)
    elif isinstance(widget, QAbstractButton):
        checked = pal["primary"] if widget.property("glyphChecked") else None
        widget.setIcon(icons.icon(str(name), colour, size, checked))
        widget.setIconSize(QSize(size, size))


def glyph_chip(name: str, parent=None, size: int = 36, colour_key: str = "primary") -> QLabel:
    """A small glass chip holding one icon - the marker on a dashboard metric card."""
    label = QLabel(parent)
    label.setObjectName("kpiIcon")
    label.setProperty(GLYPH, name)
    label.setProperty(GLYPH_COLOUR, colour_key)
    label.setProperty(GLYPH_SIZE, size)
    _paint_glyph(label)
    return label


def glyph_button(button, name: str, size: int = 20, colour_key: str = "sidebar_text", checked_variant: bool = True):
    """Give a button a drawn icon that follows the palette (checked buttons get the accent colour)."""
    button.setProperty(GLYPH, name)
    button.setProperty(GLYPH_COLOUR, colour_key)
    button.setProperty(GLYPH_SIZE, size)
    button.setProperty("glyphChecked", checked_variant)
    _paint_glyph(button)
    return button


def refresh_glyphs(root: QWidget) -> None:
    """Re-paint every remembered glyph under ``root`` - called when the theme changes."""
    for widget in (*root.findChildren(QLabel), *root.findChildren(QAbstractButton)):
        _paint_glyph(widget)


def reduce_motion() -> bool:
    """True when the operating system asks for fewer animations; the page fade is skipped then.

    Windows exposes this as 'animation effects' (SPI_GETVISUALUSEREXPERIENCE). Anywhere else the answer is no.
    """
    global _motion_preference
    if _motion_preference is None:
        wanted = False
        try:
            import ctypes

            value = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1901, 0, ctypes.byref(value), 0):
                wanted = value.value == 0
        except Exception:  # pragma: no cover - not Windows, or the call is unavailable
            wanted = False
        _motion_preference = wanted
    return _motion_preference


def parse_typed_date(text: str) -> Optional[QDate]:
    """Parse what a person types into a date box. Accepts 07-09-2026, 7/9/2026, 7.9.26, 07092026.

    Returns None when the text is not a real date."""
    t = text.strip().replace("/", "-").replace(".", "-").replace(" ", "-")
    if not t:
        return None
    if t.isdigit():
        if len(t) == 8:
            t = f"{t[0:2]}-{t[2:4]}-{t[4:8]}"
        elif len(t) == 6:
            t = f"{t[0:2]}-{t[2:4]}-20{t[4:6]}"
        else:
            return None
    parts = t.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    d, m, y = (int(p) for p in parts)
    if y < 100:
        y += 2000
    q = QDate(y, m, d)
    return q if q.isValid() else None


class DateField(QWidget):
    """Date box: type DD-MM-YYYY freely (Ctrl+A / select-all and retype works) or use the calendar button.

    Drop-in replacement for the old QDateEdit: date(), setDate(), dateChanged, setMaximumDate(), text_dmy(), set_iso().
    """

    dateChanged = Signal(QDate)

    def __init__(self, allow_future: bool = False, parent=None):
        super().__init__(parent)
        self.allow_future = allow_future
        self._min = MIN_DATE
        self._max = QDate(2100, 12, 31)
        self._date = QDate.currentDate()
        self._followed_today = QDate.currentDate()  # the 'today' this box was last set to / moved along with
        self._user_set = False                      # True once a person typed or picked a date themselves
        self._invalid = False                       # True while the typed text is not an acceptable date

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("DD-MM-YYYY")
        self.edit.setMaxLength(10)
        self.edit.setMinimumWidth(100)
        self.edit.editingFinished.connect(self._commit)
        self.button = QToolButton()
        self.button.setToolTip("Open calendar")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        glyph_button(self.button, "calendar", size=16, colour_key="primary", checked_variant=False)
        self.button.clicked.connect(self._open_calendar)
        self.setFocusProxy(self.edit)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

        self._calendar: Optional[QCalendarWidget] = None
        self.set_today()

    # -- limits -------------------------------------------------------------
    def setMinimumDate(self, d: QDate) -> None:
        self._min = d

    def setMaximumDate(self, d: QDate) -> None:
        self._max = d
        if self._date > d:
            self.setDate(d)

    def setEnabled(self, on: bool) -> None:  # keep both child widgets in step
        super().setEnabled(on)

    # -- value --------------------------------------------------------------
    def date(self) -> QDate:
        self._commit()
        return self._date

    def setDate(self, d: QDate) -> None:
        if not d.isValid():
            return
        d = max(self._min, min(self._effective_max(), d))
        changed = d != self._date
        self._date = d
        self.edit.setText(d.toString(DISPLAY_FORMAT))
        self._set_invalid("")
        if changed:
            self.dateChanged.emit(d)

    def _effective_max(self) -> QDate:
        """Latest date allowed. For boxes that refuse future dates this is always the real today (never stale)."""
        return self._max if self.allow_future else min(self._max, QDate.currentDate())

    def _set_invalid(self, message: str) -> None:
        """Show (or clear) a red border and a tooltip. The typed text is kept so the person can correct it."""
        invalid = bool(message)
        self.edit.setToolTip(message)
        if invalid == self._invalid and self.edit.property("invalid") == invalid:
            return
        self._invalid = invalid
        self.edit.setProperty("invalid", invalid)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)

    def _commit(self) -> None:
        """Read the typed text. A bad date is NOT replaced behind the person's back: it is marked red and
        ``text_dmy()`` hands the typed text on, so saving shows a clear 'not a valid date' message."""
        text = self.edit.text()
        q = parse_typed_date(text)
        limit = self._effective_max()
        if q is not None and self._min <= q <= limit:
            if q != self._date:
                self._user_set = True
            self.setDate(q)
            self.edit.setText(self._date.toString(DISPLAY_FORMAT))
            return
        if not text.strip():
            self._set_invalid("The date is empty - type it as DD-MM-YYYY")
        elif q is None:
            self._set_invalid("Not a valid date - type it as DD-MM-YYYY")
        elif q > limit:
            self._set_invalid(f"The date cannot be after {limit.toString(DISPLAY_FORMAT)}")
        else:
            self._set_invalid(f"The date cannot be before {self._min.toString(DISPLAY_FORMAT)}")

    def _open_calendar(self) -> None:
        self._commit()
        cal = QCalendarWidget()
        cal.setWindowFlags(Qt.WindowType.Popup)
        cal.setGridVisible(True)
        cal.setMinimumDate(self._min)
        cal.setMaximumDate(self._effective_max())
        cal.setSelectedDate(self._date)
        cal.clicked.connect(lambda d: (self.setDate(d), self._mark_user_set(), cal.close()))
        cal.activated.connect(lambda d: (self.setDate(d), self._mark_user_set(), cal.close()))
        self._calendar = cal  # keep a reference while it is open
        cal.move(self.mapToGlobal(self.rect().bottomLeft()))
        cal.show()

    # -- helpers used by the rest of the app ---------------------------------
    def _mark_user_set(self) -> None:
        self._user_set = True

    def set_today(self) -> None:
        today = QDate.currentDate()
        if not self.allow_future:
            self._max = today
        self._followed_today = today
        self._user_set = False
        self.setDate(today)

    def refresh_limits(self) -> None:
        """Called regularly. After midnight a box nobody has touched moves on to the new day, and the latest
        allowed date moves with it (so typing today's date is never refused on a window left open overnight)."""
        today = QDate.currentDate()
        if not self.allow_future:
            self._max = today
        if today != self._followed_today:
            if not self._user_set and self._date == self._followed_today and not self._invalid:
                self.setDate(today)
            self._followed_today = today

    def text_dmy(self) -> str:
        self._commit()
        if self._invalid:
            return self.edit.text().strip()  # let the service reject it with a clear message
        return self._date.toString(DISPLAY_FORMAT)

    def set_iso(self, iso: str) -> None:
        self.setDate(QDate.fromString(iso, "yyyy-MM-dd"))


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setHighlightSections(False)
    table.setWordWrap(False)
    return table


def set_cell(table: QTableWidget, row: int, col: int, text: str, right: bool = False,
             data=None, color: Optional[str] = None, bold: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    if color:
        item.setForeground(QBrush(QColor(color)))
    if bold:
        font = QFont(item.font())
        font.setBold(True)
        item.setFont(font)
    item.setToolTip(text)
    table.setItem(row, col, item)
    return item


def selected_id(table: QTableWidget, col: int = 0) -> Optional[int]:
    rows = table.selectionModel().selectedRows()
    if not rows:
        return None
    item = table.item(rows[0].row(), col)
    return None if item is None else item.data(Qt.ItemDataRole.UserRole)


def fit_columns(table: QTableWidget, stretch_col: int) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)


def populate_customer_combo(combo: QComboBox, customers, keep_id=None, include_all: bool = False) -> None:
    """Fill a combo with customers; item data = customer id (None for 'All customers')."""
    if keep_id is None and combo.currentIndex() >= 0:
        keep_id = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    # The longest customer name in the shop must not decide how wide the window has to be; the popup still shows it all.
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(14)
    if include_all:
        combo.addItem("All customers", None)
    for s in customers:
        combo.addItem(s.customer.name, s.customer.id)
    index = combo.findData(keep_id) if keep_id is not None else -1
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.count():
        combo.setCurrentIndex(0)
    combo.blockSignals(False)


# --------------------------------------------------------------------------- layout helpers
def page_header(title: str, subtitle: str = "") -> QWidget:
    """Title + subtitle. Both wrap, otherwise one long sentence decides the app's minimum window width."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    h1 = QLabel(title)
    h1.setObjectName("h1")
    h1.setWordWrap(True)
    layout.addWidget(h1)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return box


def primary_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("primary")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def plain_button(text: str, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if danger:
        b.setObjectName("danger")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def button_grid(buttons, columns: int, spacing: int = 8) -> QGridLayout:
    """Action buttons in aligned columns.

    A single row of nine buttons asks for more width than a maximised window on a Full HD screen, and Qt answers by
    clipping the captions. Wrapping them into columns keeps every caption readable at any window size.
    """
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(spacing)
    for index, button in enumerate(buttons):
        grid.addWidget(button, index // columns, index % columns, Qt.AlignmentFlag.AlignLeft)
    grid.setColumnStretch(columns, 1)
    return grid


class Card(QFrame):
    """White rounded panel with an optional title."""

    glass = True        # False for panels that repeat inside a scroll area (the shadow would stack and crawl)

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        if title:
            label = QLabel(title)
            label.setObjectName("h2")
            self.body.addWidget(label)
        if self.glass:
            glass_shadow(self)


class KpiCard(QFrame):
    """Dashboard metric tile: the figure, one supporting line, and a drawn glyph in the corner."""

    def __init__(self, title: str, glyph: str = "", glyph_colour: str = "primary", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)
        self.title = QLabel(title.upper())
        self.title.setObjectName("kpiTitle")
        self.title.setWordWrap(True)
        self.value = QLabel("-")
        self.value.setObjectName("kpiValue")
        self.caption = QLabel("")
        self.caption.setObjectName("kpiCaption")
        self.caption.setWordWrap(True)
        self.badge = glyph_chip(glyph, parent=self, size=32, colour_key=glyph_colour) if glyph else None
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(self.title, 1)
        if self.badge is not None:
            head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        glass_shadow(self)

    def set(self, value: str, caption: str = "", kind: str = "") -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        self.value.setStyleSheet(
            f"color: {theme.current()[kind]};" if kind in ("danger", "success", "warning") else "")


# --------------------------------------------------------------------------- entry form
class EntryForm(QWidget):
    """Fields of one ledger entry. Used by Quick Entry and by the edit dialog."""

    def __init__(self, with_customer: bool, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # A long customer name must not decide how wide the whole window has to be.
        self.customer.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.customer.setMinimumContentsLength(14)
        if with_customer:
            self.new_customer_button = QPushButton("+ New customer")
            row = QHBoxLayout()
            row.addWidget(self.customer, 1)
            row.addWidget(self.new_customer_button)
            form.addRow("Customer", row)

        self.udhaar = QRadioButton(f"{UDHAAR_LABEL}  \u2013 customer took on credit")
        self.jama = QRadioButton(f"{JAMA_LABEL}  \u2013 customer paid")
        self.udhaar.setChecked(True)
        type_row = QVBoxLayout()          # stacked: side by side these two captions set the window's minimum width
        type_row.setSpacing(6)
        type_row.addWidget(self.udhaar)
        type_row.addWidget(self.jama)
        form.addRow("Type", type_row)

        self.date = DateField()
        self.date.setToolTip("Type the date as DD-MM-YYYY (select all and retype works) or use the calendar button.")
        form.addRow("Date", self.date)

        self.item = QLineEdit()
        self.item.setPlaceholderText("e.g. Gold chain repair, Previous payment")
        self.item.setMaxLength(200)
        form.addRow("Item / description", self.item)

        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("optional")
        self.rate = QLineEdit()
        self.rate.setPlaceholderText("optional (₹)")
        qr = QHBoxLayout()
        qr.addWidget(QLabel("Qty"))
        qr.addWidget(self.quantity)
        qr.addWidget(QLabel("× Rate"))
        qr.addWidget(self.rate)
        form.addRow("Quantity × Rate", qr)

        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.amount_hint = QLabel("")
        self.amount_hint.setObjectName("muted")
        amount_row = QHBoxLayout()
        amount_row.addWidget(self.amount, 1)
        amount_row.addWidget(self.amount_hint)
        form.addRow("Amount (₹)", amount_row)

        self.has_due = QCheckBox("Promised payment date")
        self.due = DateField(allow_future=True)
        self.due.setEnabled(False)
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_due)
        due_row.addWidget(self.due)
        due_row.addStretch(1)
        form.addRow("Due date", due_row)

        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(36)
        self.notes.setPlaceholderText("Optional - long text scrolls inside the box")
        form.addRow("Notes", self.notes)

        self.quantity.textChanged.connect(self._recalculate)
        self.rate.textChanged.connect(self._recalculate)
        self.has_due.toggled.connect(self._due_toggled)
        self.udhaar.toggled.connect(self._type_changed)

    # -- behaviour --------------------------------------------------------
    def _due_toggled(self, on: bool) -> None:
        self.due.setEnabled(on and self.udhaar.isChecked())
        if on:  # sensible default: 30 days after the transaction date (set_values overrides it afterwards)
            self.due.setDate(self.date.date().addDays(30))

    def _type_changed(self, _checked: bool) -> None:
        is_udhaar = self.udhaar.isChecked()
        self.has_due.setEnabled(is_udhaar)
        self.due.setEnabled(is_udhaar and self.has_due.isChecked())

    def _recalculate(self) -> None:
        q, r = self.quantity.text().strip(), self.rate.text().strip()
        if q and r:
            try:
                amount = money.compute_amount_paise(money.parse_quantity(q), money.rupees_to_paise(r, "Rate"))
            except money.MoneyError:
                self.amount.setReadOnly(False)
                self.amount_hint.setText("")
                return
            self.amount.setText(money.plain_amount(amount))
            self.amount.setReadOnly(True)
            self.amount_hint.setText("= Qty × Rate")
        else:
            self.amount.setReadOnly(False)
            self.amount_hint.setText("")

    def customer_id(self) -> int:
        text = self.customer.currentText().strip()
        if not text:
            raise KhataError("Select a customer")
        index = self.customer.findText(text, Qt.MatchFlag.MatchFixedString)  # case-insensitive exact match
        if index < 0:
            raise KhataError(f"No customer named '{text}'. Pick one from the list or use '+ New customer'.")
        return self.customer.itemData(index)

    def select_customer(self, customer_id: int) -> None:
        index = self.customer.findData(customer_id)
        if index >= 0:
            self.customer.setCurrentIndex(index)

    def values(self) -> dict:
        both = bool(self.quantity.text().strip() and self.rate.text().strip())
        is_udhaar = self.udhaar.isChecked()
        return {
            "txn_type": "UDHAAR" if is_udhaar else "JAMA",
            "txn_date": self.date.text_dmy(),
            "item": self.item.text(),
            "amount": None if both else self.amount.text(),
            "quantity": self.quantity.text(),
            "rate": self.rate.text(),
            "notes": self.notes.toPlainText(),
            "due_date": self.due.text_dmy() if (is_udhaar and self.has_due.isChecked()) else None,
        }

    def set_values(self, txn) -> None:
        (self.udhaar if txn.txn_type == "UDHAAR" else self.jama).setChecked(True)
        self.date.setMaximumDate(QDate.currentDate())
        self.date.set_iso(txn.txn_date)
        self.item.setText(txn.item)
        self.quantity.setText(txn.quantity or "")
        self.rate.setText(money.plain_amount(txn.rate_paise) if txn.rate_paise else "")
        self.amount.setText(money.plain_amount(txn.amount_paise))
        self.notes.setPlainText(txn.notes)
        if txn.due_date:
            self.has_due.setChecked(True)
            self.due.set_iso(txn.due_date)
        else:
            self.has_due.setChecked(False)
        self._type_changed(True)
        self._recalculate()

    def clear_for_next(self) -> None:
        """Keep customer, type and date; clear the rest so the next entry can be typed straight away."""
        self.item.clear()
        self.quantity.clear()
        self.rate.clear()
        self.amount.clear()
        self.amount.setReadOnly(False)
        self.amount_hint.setText("")
        self.notes.clear()
        self.has_due.setChecked(False)
        self.item.setFocus()
