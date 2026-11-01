"""Daily Udhaar-Jama Summary page: live figures and every transaction of one chosen day."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .. import dates, exports, money
from ..labels import JAMA_WORD, UDHAAR_WORD, type_label
from ..service import BASIS_ENTERED, BASIS_TXN_DATE, KhataError, filter_daily_rows
from .widgets import (
    Card, DateField, KpiCard, fit_columns, make_table, page_header, plain_button, primary_button, set_cell,
    success_color, danger_color,
)

_SORTS = (("Time", "time"), ("Customer name", "name"), ("Amount", "amount"))
_TYPES = (("All entries", None), (f"{UDHAAR_WORD} only", "UDHAAR"), (f"{JAMA_WORD} only", "JAMA"))
_BASES = (("Dated on this day", BASIS_TXN_DATE), ("Entered on this day (incl. back-dated)", BASIS_ENTERED))


class DailySummaryPage(QWidget):
    """``window`` is the MainWindow (service, shop name, ledger opening, printing and export helpers)."""

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.svc = window.svc
        self._summary = None
        self._shown: list = []
        self.setObjectName("page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addWidget(page_header(f"Daily {UDHAAR_WORD}-{JAMA_WORD} Summary",
                                     "Every transaction of one day, calculated live from the saved entries"))

        # --- date + basis + actions
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Date"))
        self.date_field = DateField(allow_future=False)
        self.date_field.dateChanged.connect(self.refresh)
        bar.addWidget(self.date_field)
        today_btn = plain_button("Today")
        today_btn.clicked.connect(self._go_today)
        bar.addWidget(today_btn)
        self.basis = QComboBox()
        for label, key in _BASES:
            self.basis.addItem(label, key)
        self.basis.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.basis)
        bar.addStretch(1)
        for text, slot in (("Refresh", self.refresh), ("Print…", self._print), ("PDF", lambda: self._export("pdf")),
                           ("Excel", lambda: self._export("xlsx"))):
            b = plain_button(text)
            b.clicked.connect(lambda _=False, f=slot: f())
            bar.addWidget(b)
        layout.addLayout(bar)

        # --- statistics
        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {
            "jama": KpiCard(f"Total {JAMA_WORD}", "credit", "bar_jama"),
            "udhaar": KpiCard(f"Total {UDHAAR_WORD}", "debit", "bar_udhaar"),
            "net": KpiCard(f"Net daily difference", "net"),
            "count": KpiCard("Total transactions", "summary"),
            "customers": KpiCard("Customers", "users"),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)
        layout.addLayout(grid)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        # --- filters
        filters = QHBoxLayout()
        self.type_combo = QComboBox()
        for label, key in _TYPES:
            self.type_combo.addItem(label, key)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search customer name…")
        self.search.setClearButtonEnabled(True)
        self.sort_combo = QComboBox()
        for label, key in _SORTS:
            self.sort_combo.addItem("Sort by " + label.lower(), key)
        self.order_combo = QComboBox()
        self.order_combo.addItem("Ascending", False)
        self.order_combo.addItem("Descending", True)
        for w in (self.type_combo, self.sort_combo, self.order_combo):
            w.currentIndexChanged.connect(self._apply_filters)
        # Long item text must not decide the page's minimum width; the popup still shows all of it.
        for combo in (self.basis, self.type_combo, self.sort_combo, self.order_combo):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(15)
        self.search.textChanged.connect(self._apply_filters)
        filters.addWidget(self.type_combo)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.sort_combo)
        filters.addWidget(self.order_combo)
        layout.addLayout(filters)

        # --- table
        card = Card("Transactions")
        self.table = make_table(["Customer", "Type", "Amount", "Date", "Time", "Item / Description"])
        fit_columns(self.table, 5)
        self.table.doubleClicked.connect(lambda *_: self._open_ledger())
        card.body.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.footer = QLabel("")
        self.footer.setObjectName("muted")
        row.addWidget(self.footer, 1)
        open_btn = primary_button("Open customer ledger")
        open_btn.clicked.connect(self._open_ledger)
        row.addWidget(open_btn)
        card.body.addLayout(row)
        layout.addWidget(card, 1)

    # ------------------------------------------------------------ data
    def _day_iso(self) -> str | None:
        try:
            return dates.parse_date(self.date_field.text_dmy())
        except (dates.DateError, ValueError):
            return None

    def _go_today(self) -> None:
        self.date_field.set_today()   # emits dateChanged when the day actually changes
        self.refresh()

    def refresh(self, *_args) -> None:
        """Re-read the database (called after any entry is added, edited or deleted, and on opening the page)."""
        day = self._day_iso()
        if day is None:
            self.note.setText("Enter a valid date (DD-MM-YYYY).")
            return
        try:
            s = self.svc.daily_summary(day, self.basis.currentData())
        except KhataError as exc:
            self.note.setText(str(exc))
            return
        self._summary = s
        self.cards["jama"].set(money.format_inr(s.total_jama), f"{JAMA_WORD} received on this day", "success")
        self.cards["udhaar"].set(money.format_inr(s.total_udhaar), f"new {UDHAAR_WORD} given on this day", "danger")
        self.cards["net"].set(money.format_inr(s.net_difference), f"{JAMA_WORD} minus {UDHAAR_WORD} (not cash balance)",
                              "success" if s.net_difference > 0 else ("danger" if s.net_difference < 0 else ""))
        self.cards["count"].set(str(s.transaction_count), "entries on this day")
        self.cards["customers"].set(str(s.customer_count), "different customers")
        self.note.setText(f"Only {dates.format_date(day)} – not outstanding balances; open a Ledger for those.")
        self.note.setToolTip("Figures cover only this day. They are not a customer's outstanding balance "
                             f"or lifetime {JAMA_WORD} total - open the ledger for those.")
        self._apply_filters()

    def current_filter_text(self) -> str:
        parts = [self.type_combo.currentText()]
        if self.search.text().strip():
            parts.append(f'name contains "{self.search.text().strip()}"')
        return ", ".join(parts)

    def _apply_filters(self, *_args) -> None:
        if self._summary is None:
            return
        self._shown = filter_daily_rows(self._summary.rows, self.type_combo.currentData(), self.search.text(),
                                        self.sort_combo.currentData(), bool(self.order_combo.currentData()))
        self.table.setRowCount(len(self._shown))
        for i, r in enumerate(self._shown):
            colour = danger_color() if r.txn_type == "UDHAAR" else success_color()
            set_cell(self.table, i, 0, r.customer_name, data=r.customer_id)
            set_cell(self.table, i, 1, type_label(r.txn_type), color=colour, bold=True)
            set_cell(self.table, i, 2, money.format_inr(r.amount_paise), right=True, color=colour, bold=True)
            set_cell(self.table, i, 3, dates.format_date(r.txn_date))
            set_cell(self.table, i, 4, r.time[:5])
            set_cell(self.table, i, 5, r.item)
        self.footer.setText(f"Showing {len(self._shown)} of {self._summary.transaction_count} entries")

    # ------------------------------------------------------------ actions
    def _open_ledger(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._shown):
            self.window_.info("Select an entry", "Select a row first, then open that customer's ledger.")
            return
        self.window_.open_ledger_for(self._shown[row].customer_id)

    def _print(self) -> None:
        if self._summary is None:
            return
        html = exports.daily_summary_html(self._summary, self._shown, self.window_.shop_name(),
                                          self.current_filter_text())
        self.window_.print_html(html, f"Daily Summary {dates.format_date(self._summary.day)}")

    def _export(self, kind: str) -> None:
        if self._summary is None:
            return
        s, rows = self._summary, list(self._shown)
        writer = exports.export_daily_summary_pdf if kind == "pdf" else exports.export_daily_summary_xlsx
        name = f"Daily_Summary_{s.day}.{kind}"
        self.window_._run_export("Export daily summary", name, kind,
                                 lambda path: writer(s, rows, path, self.window_.shop_name(),
                                                     self.current_filter_text()))
