"""Daily Entry page: one block per customer, with as many products as you like inside it.

Each customer block = Type, Customer, Date  +  any number of product lines (Product, Quantity, Rate, Total)  +  one big
Notes box (scrolls when the text is long).  Every product line becomes one ledger entry.
Total of a line = Quantity x Rate.  Leave Quantity empty and the Total is simply the amount typed (use this for payments).
The Notes of a block are stored with the first product line that gets added (so a note is not repeated on every line).

Rows added here do NOT go into the customer's ledger straight away. They appear in the "Waiting" panel on the right
and are saved automatically 24 hours later. Until then a row can be deleted (e.g. the customer brought the goods
back) or saved early with "Save now".
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QScroller, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .. import dates, money, viewmodel, voice
from ..labels import JAMA_LABEL, QUICK_PAYMENT_ITEM, UDHAAR_LABEL, type_label
from ..service import (
    MAX_TEXT, PENDING_HOURS, CreditLimitError, DuplicateEntryError, KhataError, KhataService,
)
from .widgets import Card, DateField, button_grid, glass_shadow, plain_button, primary_button

START_CARDS = 1
START_LINES = 1
QTY_W, RATE_W, TOTAL_W, REMOVE_W = 90, 120, 120, 34
NOTES_MIN_H, NOTES_MAX_H = 40, 200
DRAFT_FILE = "daily_entry_draft.json"

PANEL_STRETCH, BLOCKS_STRETCH = 7, 5      # expanded: the waiting panel takes the bigger share of the page
MIN_PANEL_WIDTH, RAIL_WIDTH = 380, 48
SETTING_PANEL_COLLAPSED = "daily_pending_collapsed"
WIDGET_MAX = 16777215


def row_total_paise(quantity_text: str, amount_text: str) -> int | None:
    """Automatic total of one line in paise, or None when the numbers are blank / not valid yet.

    Quantity and Rate both filled -> Quantity x Rate.  Only the amount filled -> that amount.
    """
    amount_text = (amount_text or "").strip()
    quantity_text = (quantity_text or "").strip()
    if not amount_text:
        return None
    try:
        amount = money.rupees_to_paise(amount_text)
        if not quantity_text:
            return amount
        return money.compute_amount_paise(money.parse_quantity(quantity_text), amount)
    except money.MoneyError:
        return None


def line_is_empty(product: str, qty: str, amount: str) -> bool:
    return not ((product or "").strip() or (qty or "").strip() or (amount or "").strip())


def line_values(txn_type: str, txn_date: str, product: str, qty: str, amount: str, notes: str = "") -> dict:
    """The arguments of ``KhataService.add_pending_entry`` for one product line (without ``customer_id``)."""
    qty = (qty or "").strip()
    amount = (amount or "").strip()
    product = (product or "").strip()
    if not product and txn_type == "JAMA":
        product = QUICK_PAYMENT_ITEM
    both = bool(qty and amount)
    return {
        "txn_type": txn_type,
        "txn_date": txn_date,
        "item": product,
        "quantity": qty,
        "rate": amount if both else "",
        "amount": None if both else amount,
        "notes": (notes or "").strip(),
    }


class _NoWheelCombo(QComboBox):
    """A combo inside a scrolling page must not change its value when the mouse wheel only meant to scroll."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):  # noqa: N802 (Qt name)
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _enable_touch_scroll(widget) -> None:
    """Finger swipe scrolls the area on touch screens (no effect with a mouse). Never fatal."""
    try:
        QScroller.grabGesture(widget, QScroller.ScrollerGestureType.TouchGesture)
    except Exception:  # pragma: no cover - platform without touch support
        pass


def fill_customer_combo(combo: QComboBox, customers, keep_id=None) -> None:
    typed = combo.currentText()
    if keep_id is None and typed.strip():
        keep_id = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for s in customers:
        combo.addItem(s.customer.name, s.customer.id)
    index = combo.findData(keep_id) if keep_id is not None else -1
    combo.setCurrentIndex(index)           # -1 = nothing chosen yet
    if index < 0:
        # A name that is typed but not (yet) a customer stays; a chosen customer that no longer exists is cleared.
        combo.setEditText(typed if (keep_id is None and typed.strip()) else "")
    combo.blockSignals(False)


class ProductLine(QWidget):
    """One product of a customer block: Product, Quantity, Rate, Total (auto), remove."""

    changed = Signal()
    removeRequested = Signal(object)
    enterOnRate = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.product = QLineEdit()
        self.product.setPlaceholderText("Product / item")
        self.product.setMaxLength(120)
        self.qty = QLineEdit()
        self.qty.setPlaceholderText("Qty")
        self.qty.setFixedWidth(QTY_W)
        self.rate = QLineEdit()
        self.rate.setPlaceholderText("Rate 0.00")
        self.rate.setFixedWidth(RATE_W)
        self.total = QLineEdit()
        self.total.setReadOnly(True)
        self.total.setPlaceholderText("auto")
        self.total.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.total.setFixedWidth(TOTAL_W)
        self.remove = plain_button("✕")
        self.remove.setObjectName("rowDanger")   # compact: the wide button padding would swallow the glyph
        self.remove.setToolTip("Remove this product")
        self.remove.setFixedWidth(REMOVE_W)
        self.remove.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.product, 1)
        layout.addWidget(self.qty)
        layout.addWidget(self.rate)
        layout.addWidget(self.total)
        layout.addWidget(self.remove)

        self.qty.textChanged.connect(self._recalc)
        self.rate.textChanged.connect(self._recalc)
        self.product.textChanged.connect(lambda _t: self.changed.emit())
        self.remove.clicked.connect(lambda _c=False: self.removeRequested.emit(self))
        self.product.returnPressed.connect(self.qty.setFocus)
        self.qty.returnPressed.connect(self.rate.setFocus)
        self.rate.returnPressed.connect(lambda: self.enterOnRate.emit(self))

    def _recalc(self, _text=None) -> None:
        paise = row_total_paise(self.qty.text(), self.rate.text())
        self.total.setText("" if paise is None else money.format_inr(paise))
        self.changed.emit()

    def is_empty(self) -> bool:
        return line_is_empty(self.product.text(), self.qty.text(), self.rate.text())

    def total_paise(self) -> int:
        return row_total_paise(self.qty.text(), self.rate.text()) or 0

    def clear(self) -> None:
        for edit in (self.product, self.qty, self.rate):
            edit.clear()


class CustomerBlock(Card):
    """Type + Customer + Date, many product lines, and one big scrolling Notes box."""

    glass = False      # these stack inside a scroll area: one shadow per block would crawl while scrolling

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, customers, parent=None):
        super().__init__(parent=parent)
        self.type_box = _NoWheelCombo()
        self.type_box.addItem(UDHAAR_LABEL, "UDHAAR")
        self.type_box.addItem(JAMA_LABEL, "JAMA")
        # Only a couple of words long, but the full label decides the width of the whole page unless capped.
        self.type_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.type_box.setMinimumContentsLength(7)

        self.customer = _NoWheelCombo()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # A long customer name must not dictate how wide this block (and therefore the page) has to be.
        self.customer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.customer.setMinimumContentsLength(10)
        self.customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.customer.lineEdit().setPlaceholderText("Customer")
        fill_customer_combo(self.customer, customers)

        self.date = DateField()
        self.remove_block = plain_button("Remove", danger=True)
        self.remove_block.setToolTip("Remove this whole customer block")
        self.remove_block.clicked.connect(lambda _c=False: self.removeRequested.emit(self))

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.type_box)
        top.addWidget(self.customer, 1)
        top.addWidget(self.date)
        top.addWidget(self.remove_block)
        self.body.addLayout(top)

        headings = QHBoxLayout()
        headings.setContentsMargins(0, 0, 0, 0)
        headings.setSpacing(8)
        for text, width in (("Product", None), ("Quantity", QTY_W), ("Rate (per unit)", RATE_W),
                            ("Total (auto)", TOTAL_W), ("", REMOVE_W)):
            label = QLabel(text)
            label.setObjectName("muted")
            if width:
                label.setFixedWidth(width)
            headings.addWidget(label, 0 if width else 1)
        self.body.addLayout(headings)

        self.lines_layout = QVBoxLayout()
        self.lines_layout.setSpacing(6)
        self.body.addLayout(self.lines_layout)
        self.lines: list[ProductLine] = []

        self.add_product = plain_button("+ Add product")
        self.add_product.setToolTip("Another product for the same customer")
        self.add_product.clicked.connect(lambda _c=False: self.add_line(focus=True))
        self.block_total = QLabel("")
        self.block_total.setObjectName("h2")
        foot = QHBoxLayout()
        foot.addWidget(self.add_product)
        foot.addStretch(1)
        foot.addWidget(self.block_total)
        self.body.addLayout(foot)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes (optional) - long text scrolls inside the box")
        self.notes.setFixedHeight(NOTES_MIN_H)
        self.notes.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.notes.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.notes.textChanged.connect(self._limit_notes)
        self.notes.document().documentLayout().documentSizeChanged.connect(lambda _s: self._fit_notes())
        _enable_touch_scroll(self.notes.viewport())
        self.body.addWidget(self.notes)

        self.type_box.currentIndexChanged.connect(lambda _i: self.changed.emit())
        for _ in range(START_LINES):
            self.add_line()

    # ---------------------------------------------------------------- lines
    def add_line(self, focus: bool = False) -> ProductLine:
        line = ProductLine()
        self.lines.append(line)
        self.lines_layout.addWidget(line)
        line.changed.connect(self._update_total)
        line.removeRequested.connect(self.remove_line)
        line.enterOnRate.connect(self._enter_on_rate)
        self._update_total()
        if focus:
            line.product.setFocus()
        return line

    def remove_line(self, line: ProductLine) -> None:
        if line not in self.lines:
            return
        if len(self.lines) == 1:
            line.clear()           # the block always keeps at least one line
        else:
            self.lines.remove(line)
            self.lines_layout.removeWidget(line)
            line.setParent(None)
            line.deleteLater()
        self._update_total()

    def _enter_on_rate(self, line: ProductLine) -> None:
        index = self.lines.index(line) if line in self.lines else -1
        if index == len(self.lines) - 1:
            self.add_line(focus=True)
        elif index >= 0:
            self.lines[index + 1].product.setFocus()

    def filled_lines(self) -> list[ProductLine]:
        return [ln for ln in self.lines if not ln.is_empty()]

    # ---------------------------------------------------------------- state
    def txn_type(self) -> str:
        return self.type_box.currentData()

    def notes_text(self) -> str:
        return self.notes.toPlainText().strip()

    def is_empty(self) -> bool:
        return not self.filled_lines() and not self.notes_text()

    def total_paise(self) -> int:
        return sum(ln.total_paise() for ln in self.lines)

    def _fit_notes(self) -> None:
        """Notes box stays one line tall until text needs more room, then grows up to NOTES_MAX_H.

        The height comes from QPlainTextDocumentLayout.blockBoundingRect (pixel heights, wrapping included);
        documentSize().height() is a block count, not pixels.
        """
        doc = self.notes.document()
        layout = doc.documentLayout()
        total = 0.0
        block = doc.begin()
        while block.isValid():
            total += layout.blockBoundingRect(block).height()
            block = block.next()
        height = int(total) + 2 * self.notes.frameWidth() + 4
        self.notes.setFixedHeight(max(NOTES_MIN_H, min(NOTES_MAX_H, height)))

    def _limit_notes(self) -> None:
        text = self.notes.toPlainText()
        if len(text) > MAX_TEXT:
            self.notes.blockSignals(True)
            self.notes.setPlainText(text[:MAX_TEXT])
            cursor = self.notes.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.notes.setTextCursor(cursor)
            self.notes.blockSignals(False)
        self._fit_notes()
        self.changed.emit()

    def _update_total(self) -> None:
        filled = len(self.filled_lines())
        self.block_total.setText(f"{filled} product(s)   Total {money.format_inr(self.total_paise())}" if filled else "")
        self.changed.emit()


class _VerticalLabel(QLabel):
    """Text reading bottom-to-top, for the collapsed rail."""

    def sizeHint(self):  # noqa: N802 (Qt name)
        hint = super().sizeHint()
        return QSize(hint.height(), hint.width())

    def paintEvent(self, event):  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(QRect(0, 0, self.height(), self.width()), self.alignment(), self.text())


class PendingRow(QFrame):
    """One waiting entry in the side panel: what it is, when it saves itself, and the two actions."""

    def __init__(self, customer_name: str, on_save, on_delete, parent=None):
        super().__init__(parent)
        self.setObjectName("pendingRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(customer_name)
        name.setObjectName("pendingName")
        name.setToolTip(customer_name)
        head.addWidget(name, 1)
        self.amount = QLabel("")
        self.amount.setObjectName("pendingAmount")
        head.addWidget(self.amount, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        meta.addWidget(self.detail, 1)
        self.when = QLabel("")
        self.when.setObjectName("muted")
        meta.addWidget(self.when, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addLayout(meta)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        save_now = QPushButton("Save now")
        save_now.setObjectName("rowAction")
        save_now.setToolTip("Put this entry into the customer's ledger right away")
        save_now.setCursor(Qt.CursorShape.PointingHandCursor)
        save_now.clicked.connect(lambda _c=False: on_save())
        delete = QPushButton("Delete")
        delete.setObjectName("rowDanger")
        delete.setToolTip("Remove this entry. It will not be saved in the ledger (e.g. goods returned)")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.clicked.connect(lambda _c=False: on_delete())
        actions.addWidget(save_now)
        actions.addWidget(delete)
        actions.addStretch(1)
        layout.addLayout(actions)

    def fill(self, p, time_left: str) -> None:
        """Show one pending entry (called again by the countdown timer, so the text stays fresh)."""
        self.amount.setText(money.format_inr(p.amount_paise))
        self.detail.setText(f"{type_label(p.txn_type)} · {p.item} · {dates.format_date(p.txn_date)}")
        self.when.setText(f"Saves in {time_left}")


class DailyEntryPage(QWidget):
    def __init__(self, service: KhataService, on_saved, info, warn, confirm, parent=None, new_customer=None):
        super().__init__(parent)
        self.service = service
        self._on_saved = on_saved
        self._info = info
        self._warn = warn
        self._confirm = confirm
        self._new_customer = new_customer  # callable(name) -> new customer id or None (None = cancelled)
        self._customers: list = []
        self._busy = False
        self._draft_ready = False          # switches on at the end of __init__ (see save_draft)
        self.blocks: list[CustomerBlock] = []

        self.blocks_layout = QVBoxLayout()
        self.blocks_layout.setContentsMargins(0, 0, 6, 0)
        self.blocks_layout.setSpacing(12)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addLayout(self.blocks_layout)
        holder_layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(holder)
        _enable_touch_scroll(self.scroll.viewport())

        self.summary = QLabel("")
        self.summary.setObjectName("h2")
        # The totals line must not set a floor on the page width: the buttons beside it matter more.
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        add_block = plain_button("+ Add customer")
        add_block.setToolTip("Another customer block")
        add_block.clicked.connect(lambda: self.add_block(focus=True))
        clear = plain_button("Clear all")
        clear.clicked.connect(self.clear_all)
        self.new_customer_button = plain_button("+ New customer")
        self.new_customer_button.setToolTip("Add a customer without leaving this page; it is selected in the block you are on")
        self.new_customer_button.clicked.connect(self._new_customer_clicked)
        self.new_customer_button.setVisible(new_customer is not None)
        self.save_button = primary_button("Add entries")
        self.save_button.setToolTip(f"Rows go to the waiting panel on the right and into the ledger after {PENDING_HOURS} hours")
        self.save_button.clicked.connect(self.save_all)

        bar = QVBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.summary)
        bar.addLayout(button_grid((add_block, clear, self.new_customer_button, self.save_button), 2))

        hint = QLabel("Total = Quantity x Rate; leave Quantity empty for a single amount (e.g. a payment). "
                      f"Entries wait in the panel on the right and are saved into the ledger after {PENDING_HOURS} hours.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)

        left_pane = QWidget()
        left = QVBoxLayout(left_pane)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        left.addWidget(self.scroll, 1)
        left.addLayout(bar)
        left.addWidget(hint)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setObjectName("dailySplit")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(10)
        self._splitter.addWidget(left_pane)
        self._splitter.addWidget(self._build_side_panel())
        self._splitter.setStretchFactor(0, BLOCKS_STRETCH)
        self._splitter.setStretchFactor(1, PANEL_STRETCH)
        self.set_panel_collapsed(self.service.get_setting(SETTING_PANEL_COLLAPSED, "0") == "1")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._splitter)

        for _ in range(START_CARDS):
            self.add_block()
        self.refresh_pending()
        self._draft_ready = True

    # ------------------------------------------------------------ waiting panel (right side)
    def _build_side_panel(self) -> QWidget:
        """The waiting panel and the thin rail it collapses into; only one of them is visible."""
        self.pending_title = QLabel("Waiting to be saved")
        self.pending_title.setObjectName("h2")
        collapse = QPushButton("\u00bb")
        collapse.setObjectName("panelToggle")
        collapse.setToolTip("Hide this panel")
        collapse.setFixedSize(26, 26)
        collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse.clicked.connect(lambda: self.set_panel_collapsed(True))
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self.pending_title, 1, Qt.AlignmentFlag.AlignTop)
        head.addWidget(collapse, 0, Qt.AlignmentFlag.AlignTop)

        self.pending_holder = QWidget()
        self.pending_rows = QVBoxLayout(self.pending_holder)
        self.pending_rows.setContentsMargins(0, 0, 2, 0)
        self.pending_rows.setSpacing(8)
        self.pending_rows.addStretch(1)
        self.pending_scroll = QScrollArea()
        self.pending_scroll.setWidgetResizable(True)
        self.pending_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pending_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pending_scroll.setWidget(self.pending_holder)
        _enable_touch_scroll(self.pending_scroll.viewport())

        self.panel = QFrame()
        self.panel.setObjectName("sidePanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(14, 12, 12, 12)
        panel_layout.setSpacing(8)
        panel_layout.addLayout(head)
        note = QLabel(f"Rows land here when you press Add entries, then go into the ledger on their own "
                      f"after {PENDING_HOURS} hours.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        panel_layout.addWidget(note)
        panel_layout.addWidget(self.pending_scroll, 1)
        glass_shadow(self.panel)

        self.rail = QFrame()
        self.rail.setObjectName("panelRail")
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 10, 8, 10)
        rail_layout.setSpacing(10)
        expand = QPushButton("\u00ab")
        expand.setObjectName("panelToggle")
        expand.setToolTip("Show the waiting list")
        expand.setFixedSize(26, 26)
        expand.setCursor(Qt.CursorShape.PointingHandCursor)
        expand.clicked.connect(lambda: self.set_panel_collapsed(False))
        self.rail_count = QLabel("")
        self.rail_count.setObjectName("railText")
        self.rail_count.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        rail_text = _VerticalLabel("Waiting")
        rail_text.setObjectName("railText")
        rail_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rail_layout.addWidget(expand, 0, Qt.AlignmentFlag.AlignHCenter)
        rail_layout.addWidget(self.rail_count, 0, Qt.AlignmentFlag.AlignHCenter)
        rail_layout.addWidget(rail_text, 0, Qt.AlignmentFlag.AlignHCenter)
        rail_layout.addStretch(1)

        container = QWidget()
        box = QHBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.panel)
        box.addWidget(self.rail)
        self._side = container
        return container

    def set_panel_collapsed(self, collapsed: bool) -> None:
        """Collapsed = thin rail; expanded = the panel takes the bigger share of the page (drag the handle for more)."""
        self.panel.setVisible(not collapsed)
        self.rail.setVisible(collapsed)
        if collapsed:
            self._side.setFixedWidth(RAIL_WIDTH)
        else:
            self._side.setMinimumWidth(MIN_PANEL_WIDTH)
            self._side.setMaximumWidth(WIDGET_MAX)
            self._splitter.setSizes([45, 55])       # setSizes normalises, so this is a share of the page
        self.service.set_setting(SETTING_PANEL_COLLAPSED, "1" if collapsed else "0")

    def _clear_pending_rows(self) -> None:
        while self.pending_rows.count() > 1:  # the stretch that keeps rows top-aligned stays
            widget = self.pending_rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # ------------------------------------------------------------ customers
    def set_customers(self, summaries) -> None:
        """Refresh the customer lists in every block (keeps what was already chosen)."""
        self._customers = list(summaries)
        for block in self.blocks:
            fill_customer_combo(block.customer, self._customers)

    # ------------------------------------------------------------ blocks
    def add_block(self, focus: bool = False) -> CustomerBlock:
        block = CustomerBlock(self._customers)
        if self.blocks:                      # same type and date as the block above; the customer is new
            previous = self.blocks[-1]
            block.type_box.setCurrentIndex(previous.type_box.currentIndex())
            block.date.setDate(previous.date.date())
        self.blocks.append(block)
        self.blocks_layout.addWidget(block)
        block.changed.connect(self._update_summary)
        block.removeRequested.connect(self.remove_block)
        self._sync_blocks_min()
        self._update_summary()
        if focus:
            block.customer.setFocus()
            self.scroll.ensureWidgetVisible(block)
        return block

    def remove_block(self, block: CustomerBlock) -> None:
        if block not in self.blocks:
            return
        if not block.is_empty() and not self._confirm("Remove customer", "Remove this customer block and everything typed in it?"):
            return
        self.blocks.remove(block)
        self.blocks_layout.removeWidget(block)
        block.setParent(None)
        block.deleteLater()
        if not self.blocks:
            self.add_block()
        QTimer.singleShot(0, self._sync_blocks_min)   # the block is really gone only after this round
        self._update_summary()

    def clear_all(self) -> None:
        if self._has_data() and not self._confirm("Clear all", "Remove everything typed on this page?"):
            return
        for block in list(self.blocks):
            self.blocks.remove(block)
            self.blocks_layout.removeWidget(block)
            block.setParent(None)
            block.deleteLater()
        for _ in range(START_CARDS):
            self.add_block()
        self._update_summary()

    def _sync_blocks_min(self) -> None:
        """Keep the blocks column wide enough for its rows: a scroll area with the horizontal bar switched off does
        not pass the content's minimum on, so the waiting panel would otherwise squeeze the blocks until they clip."""
        self.scroll.setMinimumWidth(self.scroll.widget().minimumSizeHint().width())

    def _has_data(self) -> bool:
        return any(not b.is_empty() for b in self.blocks)

    # ------------------------------------------------------------ totals
    def _update_summary(self) -> None:
        count = 0
        udhaar = jama = 0
        for block in self.blocks:
            count += len(block.filled_lines())
            if block.txn_type() == "UDHAAR":
                udhaar += block.total_paise()
            else:
                jama += block.total_paise()
        self.summary.setText(f"{count} entries   |   {UDHAAR_LABEL} {money.format_inr(udhaar)}   "
                             f"{JAMA_LABEL} {money.format_inr(jama)}")
        self.save_draft()

    # ------------------------------------------------------------ draft (crash / close protection)
    def has_unsaved(self) -> bool:
        return self._has_data()

    def draft_path(self) -> Path | None:
        """The draft file next to the database, or None when the service has no file (tests / preview)."""
        try:
            return Path(self.service.db_path).parent / DRAFT_FILE
        except AttributeError:
            return None

    def draft_data(self) -> list[dict]:
        out = []
        for block in self.blocks:
            if block.is_empty():
                continue
            out.append({
                "type": block.type_box.currentData(),
                "customer": block.customer.currentText().strip(),
                "date": block.date.date().toString("yyyy-MM-dd"),
                "lines": [{"product": ln.product.text(), "qty": ln.qty.text(), "rate": ln.rate.text()}
                          for ln in block.lines if not ln.is_empty()],
                "notes": block.notes.toPlainText(),
            })
        return out

    def save_draft(self) -> None:
        """Keep a copy of everything typed so far next to the database. Never fatal, never raises.

        Skipped while the page is still being constructed, so opening the app never wipes a draft
        left behind by the previous session before the restore question was asked.
        """
        if not self._draft_ready:
            return
        path = self.draft_path()
        if path is None:
            return
        try:
            data = self.draft_data()
            if data:
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def has_draft(self) -> bool:
        path = self.draft_path()
        return bool(path and path.is_file())

    def load_draft(self) -> int:
        """Recreate the blocks from the last draft. Returns how many blocks were restored."""
        path = self.draft_path()
        if path is None:
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, list) or not data:
            return 0
        while len(self.blocks) > len(data):        # reuse the fresh empty blocks where possible
            empty = [b for b in self.blocks if b.is_empty()]
            if not empty:
                break
            self.remove_block(empty[-1])
        restored = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            if restored < len(self.blocks):
                block = self.blocks[restored]
            else:
                block = self.add_block()
            index = block.type_box.findData(item.get("type"))
            if index >= 0:
                block.type_box.setCurrentIndex(index)
            text = str(item.get("customer") or "")
            found = block.customer.findText(text, Qt.MatchFlag.MatchFixedString)
            if found >= 0:
                block.customer.setCurrentIndex(found)
            else:
                block.customer.setEditText(text)
            iso = str(item.get("date") or "")
            if iso:
                block.date.set_iso(iso)
            lines = [ln for ln in (item.get("lines") or []) if isinstance(ln, dict)]
            while len(block.lines) < max(1, len(lines)):
                block.add_line()
            for line, values in zip(block.lines, lines):
                line.product.setText(str(values.get("product") or ""))
                line.qty.setText(str(values.get("qty") or ""))
                line.rate.setText(str(values.get("rate") or ""))
            block.notes.setPlainText(str(item.get("notes") or ""))
            restored += 1
        self._update_summary()
        return restored

    def clear_draft(self) -> None:
        path = self.draft_path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------ customers (typed names / new customer)
    def _customer_id(self, combo: QComboBox) -> int:
        text = combo.currentText().strip()
        if not text:
            raise KhataError("Select a customer")
        index = combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0 and self._new_customer is not None and self._confirm(
                "New customer", f"There is no customer named '{text}'.\n\nAdd it as a new customer now?"):
            new_id = self._new_customer(text)
            if new_id is not None:
                self._set_combo_customer(combo, new_id)
                index = combo.findData(new_id)
        if index < 0:
            raise KhataError(f"No customer named '{text}'. Pick one from the list.")
        return combo.itemData(index)

    def _set_combo_customer(self, combo: QComboBox, customer_id: int) -> None:
        index = combo.findData(customer_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _target_block(self) -> CustomerBlock:
        """The block a new customer goes into: the first block that has data but no customer, else the first block
        with no customer, else the last block."""
        no_customer = [b for b in self.blocks if not b.customer.currentText().strip()]
        for block in no_customer:
            if not block.is_empty():
                return block
        return no_customer[0] if no_customer else self.blocks[-1]

    def _new_customer_clicked(self) -> None:
        if self._new_customer is None or not self.blocks:
            return
        block = self._target_block()
        combo = block.customer
        typed = combo.currentText().strip() if combo.currentIndex() < 0 else ""
        new_id = self._new_customer(typed)
        if new_id is None:
            return
        self._set_combo_customer(block.customer, new_id)   # same widget, lists were refreshed meanwhile
        block.lines[0].product.setFocus()

    # ------------------------------------------------------------ saving
    def _add_pending(self, customer_id: int, values: dict, label: str) -> bool:
        """Queue one product line (it is saved into the ledger after 24 hours). Returns False when the person chose
        not to add it (duplicate / over limit)."""
        if values["quantity"] and not values["rate"]:
            raise KhataError("Enter the Rate (per unit) as well, or clear the Quantity")
        allow_duplicate = False
        allow_over_limit = False
        over_balance = 0
        while True:
            try:
                self.service.add_pending_entry(customer_id=customer_id, allow_duplicate=allow_duplicate,
                                               allow_over_limit=allow_over_limit, **values)
            except DuplicateEntryError as exc:
                if allow_duplicate or not self._confirm("Possible duplicate", f"{label}: {exc}\n\nAdd it again anyway?"):
                    return False
                allow_duplicate = True
                continue
            except CreditLimitError as exc:
                if allow_over_limit or not self._confirm("Credit limit exceeded", f"{label}: {exc}\n\nAdd this entry anyway?"):
                    return False
                allow_over_limit = True
                over_balance = exc.new_balance_paise   # the exact figure the box on screen just showed
                continue
            # the row is in the waiting table now, so a warning about it is safe to speak
            if allow_over_limit:
                voice.announce("entry_over_limit", customer=self.service.get_customer(customer_id).name,
                               balance=over_balance)
            elif allow_duplicate:
                voice.announce("entry_duplicate")
            return True

    def save_all(self) -> None:
        if self._busy:
            return
        blocks = [b for b in self.blocks if not b.is_empty()]
        if not blocks:
            self._info("Nothing to save", "Type at least one entry first.")
            return
        self._busy = True
        self.save_button.setEnabled(False)
        added = 0
        skipped = 0
        error: str | None = None
        try:
            for block in blocks:
                number = self.blocks.index(block) + 1
                lines = block.filled_lines()
                if not lines:
                    error = f"Customer {number}: add at least one product (or clear the notes)"
                    break
                try:
                    customer_id = self._customer_id(block.customer)
                except KhataError as exc:
                    error = f"Customer {number}: {exc}"
                    break
                stop = False
                for line in lines:
                    label = f"Customer {number}, product {block.lines.index(line) + 1}"
                    try:
                        values = line_values(block.txn_type(), block.date.text_dmy(), line.product.text(),
                                             line.qty.text(), line.rate.text(), block.notes_text())
                        if self._add_pending(customer_id, values, label):
                            added += 1
                            block.remove_line(line)      # saved: gone from the page
                            block.notes.clear()          # the notes went along with this entry; do not repeat them
                        else:
                            skipped += 1
                    except KhataError as exc:
                        error = f"{label}: {exc}"
                        stop = True
                        break
                if not block.filled_lines() and not block.notes_text():
                    self.remove_block(block)             # fully added -> block disappears (empty, so no question)
                if stop:
                    break
            if added:
                voice.announce("daily_queued", count=added)
                self._on_saved()
            self._update_summary()
            if error:
                self._warn("Cannot add", f"{error}\n\n{added} entries before it were added. "
                                         "Fix this one and press the button again.")
            else:
                message = (f"{added} entries added. They will be saved into the customers' ledgers "
                           f"automatically after {PENDING_HOURS} hours.\nUntil then you can delete them from the "
                           "Waiting panel on the right.")
                if skipped:
                    message += f"\n{skipped} entry(ies) were not added and are still on the page."
                self._info("Added", message)
        finally:
            self._busy = False
            self.save_button.setEnabled(True)

    # ------------------------------------------------------------ waiting list (24 hours)
    def refresh_pending(self) -> None:
        """Reload the waiting panel (also updates the 'Saves in' countdown)."""
        try:
            pending = self.service.list_pending()
        except KhataError:
            pending = []
        self._clear_pending_rows()
        for p in pending:
            row = PendingRow(p.customer_name,
                             on_save=lambda pid=p.id: self._save_pending_now(pid),
                             on_delete=lambda pid=p.id: self._delete_pending(pid))
            row.fill(p, viewmodel.time_left_text(self.service.seconds_until_post(p)))
            self.pending_rows.insertWidget(self.pending_rows.count() - 1, row)
        if not pending:
            empty = QLabel("Nothing waiting right now.")
            empty.setObjectName("emptyState")
            self.pending_rows.insertWidget(self.pending_rows.count() - 1, empty)
        self.pending_title.setText(f"Waiting to be saved ({len(pending)})")
        self.rail_count.setText(str(len(pending)))

    def _describe(self, p) -> str:
        return (f"{p.customer_name}\n{type_label(p.txn_type)}  {money.format_inr(p.amount_paise)}  –  {p.item}\n"
                f"Date {dates.format_date(p.txn_date)}")

    def _delete_pending(self, pending_id: int) -> None:
        try:
            p = self.service.get_pending_entry(pending_id)
        except KhataError as exc:
            self._warn("Cannot delete", str(exc))
            self.refresh_pending()
            return
        if not self._confirm("Delete entry", f"Delete this entry?\n\n{self._describe(p)}\n\n"
                                              "It will NOT be saved in the customer's ledger."):
            return
        try:
            self.service.delete_pending(pending_id)
        except KhataError as exc:
            self._warn("Cannot delete", str(exc))
        else:
            voice.announce("entry_deleted")
        self.refresh_pending()

    def _save_pending_now(self, pending_id: int) -> None:
        try:
            p = self.service.get_pending_entry(pending_id)
        except KhataError as exc:
            self._warn("Cannot save", str(exc))
            self.refresh_pending()
            return
        if not self._confirm("Save now", f"Put this entry into the customer's ledger now?\n\n{self._describe(p)}"):
            return
        try:
            self.service.post_pending(pending_id)
        except KhataError as exc:
            self._warn("Cannot save", str(exc))
            self.refresh_pending()
            return
        voice.announce("daily_posted", count=1)
        self._on_saved()  # refreshes the whole window, including this list
