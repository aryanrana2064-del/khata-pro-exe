"""Main window: glass sidebar + pages (Dashboard, Customers, New Entry, Daily Entry, Ledger, Recovery, Reports, Activity, Settings)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGraphicsOpacityEffect,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QProgressDialog, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, backup, paths, dates, exports, insights, money, pagesetup, receipt, security, updates, viewmodel, voice
from ..service import CreditLimitError, DuplicateEntryError, KhataError, KhataService
from ..voice import voice_history
from ..voice.voice_event_dispatcher import EVENT_CATALOGUE
from ..voice.voice_settings import LANGUAGE_LABELS, VoiceSettings
from ..labels import JAMA_LABEL, JAMA_WORD, UDHAAR_LABEL, UDHAAR_WORD, type_label
from . import theme
from .charts import MonthlyChart
from .daily_entry import DailyEntryPage
from .daily_summary import DailySummaryPage
from .dialogs import AuditDetailDialog, CustomerDialog, EditEntryDialog, PageSetupDialog, PinSetupDialog, LockDialog, QuickPaymentDialog, ReminderDialog
from .widgets import (
    Card, DateField, EntryForm, KpiCard, balance_color, button_grid, fit_columns, glyph_button, make_table,
    page_header, plain_button, populate_customer_combo, primary_button, reduce_motion, refresh_glyphs,
    refresh_shadows, selected_id, set_cell,
)

PAGES = (
    ("dashboard", "Dashboard"),
    ("summary", "Daily Summary"),
    ("customers", "Customers"),
    ("entry", "New Entry"),
    ("daily", "Daily Entry"),
    ("ledger", "Ledger"),
    ("recovery", "Recovery"),
    ("reports", "Reports"),
    ("activity", "Activity"),
    ("voice", "Voice"),
    ("settings", "Settings"),
)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_") or "khata"


def _documents_dir() -> str:
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())


def _set_state(widget: QWidget, state: str) -> None:
    """Switch the 'state' style property (used by the credit progress bar) and re-apply the stylesheet."""
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class UpdateCheckThread(QThread):
    """Asks GitHub for the latest version off the UI thread; an offline PC just reports the failure."""

    found = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.found.emit(updates.fetch_release())
        except updates.UpdateError as exc:
            self.failed.emit(str(exc))


class UpdateDownloadThread(QThread):
    """Downloads the installer and verifies its checksum off the UI thread."""

    progress = Signal(int, int)
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self, release, folder: Path, parent=None):
        super().__init__(parent)
        self.release = release
        self.folder = folder
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = updates.download(self.release, self.folder,
                                    progress=lambda got, total: self.progress.emit(got, total),
                                    cancelled=lambda: self._cancel)
        except updates.UpdateError as exc:
            self.failed.emit(str(exc))
            return
        self.saved.emit(str(path))


class MainWindow(QMainWindow):
    def __init__(self, service: KhataService):
        super().__init__()
        self.svc = service
        self.guard = security.PinGuard(service)
        self.setWindowTitle(f"{APP_NAME}  –  Customer Ledger")
        # 1440 clears the widest row on every page; setMinimumSize stays lower on purpose so a small laptop still
        # opens the app, with the wrapped button grids absorbing the squeeze instead of clipping captions.
        self.resize(1440, 860)
        self.setMinimumSize(1120, 640)
        self._saving = False
        self._locked = False
        self.current_key = "dashboard"
        self._audit_cache: dict[int, object] = {}
        self._available_release: object | None = None
        self._update_thread: UpdateCheckThread | None = None
        self._download_thread: UpdateDownloadThread | None = None
        self._update_dialog: QProgressDialog | None = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.page_index: dict[str, int] = {}
        builders = {
            "dashboard": self._build_dashboard, "summary": self._build_summary, "customers": self._build_customers, "entry": self._build_entry, "daily": self._build_daily,
            "ledger": self._build_ledger, "recovery": self._build_recovery, "reports": self._build_reports,
            "activity": self._build_activity, "voice": self._build_voice,
            "settings": self._build_settings,
        }
        for key, _label in PAGES:
            self.page_index[key] = self.stack.addWidget(builders[key]())

        for number, (key, _label) in enumerate(PAGES[:9], start=1):   # Ctrl+1..9
            QShortcut(QKeySequence(f"Ctrl+{number}"), self).activated.connect(lambda k=key: self.show_page(k))
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new_entry_shortcut)
        QShortcut(QKeySequence("Ctrl+J"), self).activated.connect(lambda: self._quick_payment())
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.lock_now)
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh_all)

        self._update_theme_button()
        self.refresh_all()
        self.show_page("dashboard")

        # Daily Entry rows are saved into the ledgers 24 hours after they were added; check once a minute.
        self._backup_retry_at = None  # after a failed automatic backup, wait an hour before trying again
        self._pending_timer = QTimer(self)
        self._pending_timer.setInterval(60_000)
        self._pending_timer.timeout.connect(self._minute_tick)
        self._pending_timer.start()

        # One time only: switch cloud copy on for every cloud app found on this PC (OneDrive / Google Drive).
        try:
            added = self.svc.backups.auto_cloud_setup()
        except Exception:  # noqa: BLE001 - detection must never stop the app from starting
            added = []
        if added:
            self.statusBar().showMessage("Cloud backup switched on: " + ", ".join(str(p) for p in added), 15000)
        QTimer.singleShot(0, self._offer_draft_restore)

    # ------------------------------------------------------------ shared helpers
    def info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def confirm(self, title: str, text: str, default_no: bool = True) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes)
        return box.exec() == QMessageBox.StandardButton.Yes

    def shop_name(self) -> str:
        return self.svc.get_setting("shop_name", "")

    def shop_details(self) -> str:
        return self.svc.get_setting("shop_details", "")

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # so the aurora bloom behind it paints
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        return page, layout

    def _header(self, layout: QVBoxLayout, title: str, subtitle: str = "", *actions: QWidget) -> None:
        row = QHBoxLayout()
        row.addWidget(page_header(title, subtitle), 1)
        for widget in actions:
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)

    def _export_path(self, title: str, default_name: str, pattern: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path(_documents_dir()) / default_name), pattern)
        return path or None

    def _offer_open(self, path) -> None:
        if self.confirm("Export complete", f"Saved:\n{path}\n\nOpen it now?", default_no=False):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _run_export(self, title: str, default_name: str, kind: str, writer) -> None:
        """Ask for a file name, call ``writer(path)``, report errors, offer to open the result."""
        pattern = "PDF files (*.pdf)" if kind == "pdf" else "Excel files (*.xlsx)"
        path = self._export_path(title, default_name, pattern)
        if not path:
            return
        try:
            writer(path)
        except (OSError, KhataError) as exc:
            voice.announce("export_failed")
            self.warn("Export failed", str(exc))
            return
        voice.announce("export_done")
        self._offer_open(path)

    # ------------------------------------------------------------ navigation / refresh
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(224)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 22, 14, 16)
        layout.setSpacing(4)
        logo_file = paths.asset_path("logo_small.png")
        if logo_file.is_file():
            from PySide6.QtGui import QPixmap
            logo = QLabel()
            logo.setPixmap(QPixmap(str(logo_file)).scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
            logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(logo)
            layout.addSpacing(6)
        brand = QLabel("VX7 KHATA PRO")
        brand.setObjectName("brand")
        sub = QLabel("Customer Ledger Manager")
        sub.setObjectName("brandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in PAGES:
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            glyph_button(button, key, size=18)      # every page key is also the name of its glyph
            button.clicked.connect(lambda _checked=False, k=key: self.show_page(k))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)

        # The look/lock controls sit below their own hairline so they read as a separate group.
        divider = QFrame()
        divider.setObjectName("sideDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(10)

        self.theme_button = QPushButton()
        self.theme_button.setObjectName("sideTool")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        self.lock_button = QPushButton("Lock  (Ctrl+L)")
        self.lock_button.setObjectName("sideTool")
        self.lock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_button.clicked.connect(self.lock_now)
        note = QLabel(f"Works fully offline.\nData stays on this PC.\nv{__version__}")
        note.setObjectName("sideNote")
        layout.addWidget(self.theme_button)
        layout.addWidget(self.lock_button)
        layout.addSpacing(6)
        layout.addWidget(note)
        return side

    def show_page(self, key: str) -> None:
        self.current_key = key
        self.stack.setCurrentIndex(self.page_index[key])
        self.nav_buttons[key].setChecked(True)
        self._fade_in(self.stack.currentWidget())
        self._refresh_page(key)

    def _fade_in(self, widget: QWidget) -> None:
        """A short fade as a page comes forward. The effect is dropped again afterwards, so a big table does not
        pay for it while scrolling. Skipped entirely when Windows asks for reduced motion."""
        if widget is None or reduce_motion():
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(160)
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
        self._fade_anim = anim          # the animation must outlive this method
        anim.start()

    def _new_entry_shortcut(self) -> None:
        self.show_page("entry")
        self.entry_form.item.setFocus()

    def refresh_all(self) -> None:
        summaries = self.svc.list_customers()
        populate_customer_combo(self.entry_form.customer, summaries)
        populate_customer_combo(self.ledger_customer, summaries)
        populate_customer_combo(self.report_customer, summaries, include_all=True)
        self.daily_page.set_customers(summaries)
        self.daily_page.refresh_pending()
        self._refresh_date_limits()
        self._refresh_dashboard()
        self.summary_page.refresh()
        self._refresh_customers(summaries)
        self._update_entry_context()
        self._refresh_ledger()
        self._refresh_page(self.current_key)
        self.statusBar().showMessage(f"{len(summaries)} customers  |  data file: {self.svc.db_path}")

    def _refresh_page(self, key: str) -> None:
        if key == "summary":
            self.summary_page.refresh()
        elif key == "recovery":
            self._refresh_recovery()
        elif key == "activity":
            self._refresh_activity()
        elif key == "voice":
            self._refresh_voice_settings()
        elif key == "settings":
            self._refresh_settings()

    # ------------------------------------------------------------ daily udhaar-jama summary
    def _build_summary(self) -> QWidget:
        self.summary_page = DailySummaryPage(self)
        return self.summary_page

    def open_ledger_for(self, customer_id: int) -> None:
        """Open one customer's ledger (used by the Daily Summary page)."""
        self.ledger_customer.setCurrentIndex(max(0, self.ledger_customer.findData(customer_id)))
        self._refresh_ledger()
        self.show_page("ledger")

    def print_html(self, html: str, title: str) -> None:
        """Print any report through the standard Windows print dialog flow: page setup, then preview with the
        printer chooser, A4 / A5 / custom size, orientation, margins, scaling and page selection."""
        self._preview_and_print(title, lambda: html)

    def _preview_and_print(self, title: str, build_html) -> None:
        """Page setup, then print preview (works with any printer or 'Microsoft Print to PDF').

        ``build_html`` is called every time the preview repaints. The preview toolbar has a 'Page setup...'
        button that comes back to the page setup dialog.
        """
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrintPreviewDialog

        errors: list[Exception] = []

        def paint(target) -> None:
            try:
                doc = QTextDocument()
                doc.setHtml(build_html())
                (getattr(doc, "print_", None) or doc.print)(target)
            except Exception as exc:  # never let an error escape into Qt's paint loop
                errors.append(exc)

        if not self._ask_page_setup():
            return
        sent = {"jobs": 0}   # only the preview's own Print button means a job actually went to a printer
        while True:
            # QPrintPreviewDialog only BORROWS the printer. If the QPrinter is a temporary, Python frees it at once
            # and the dialog paints into freed memory (a hard crash). Keep it alive on self until the dialog is gone.
            self._print_keepalive = self._printer_for(pagesetup.load(self.svc))
            preview = QPrintPreviewDialog(self._print_keepalive, self)
            preview.setWindowTitle(title)
            preview.resize(960, 720)
            preview.paintRequested.connect(paint)
            preview.printRequested.connect(lambda: sent.__setitem__("jobs", sent["jobs"] + 1))
            change = {"again": False}

            def reopen_setup(_checked=False, p=preview, flag=change) -> None:
                flag["again"] = True
                p.done(0)

            bars = preview.findChildren(QToolBar)
            if bars:
                bars[0].addSeparator()
                bars[0].addAction("Page setup...").triggered.connect(reopen_setup)
            preview.exec()
            preview.deleteLater()
            if errors:
                voice.announce("print_failed")
                self.warn("Cannot print", str(errors[0]))
                return
            if not change["again"] or not self._ask_page_setup():
                break
        # Qt gives no job id back, so this is "sent", not "printed on paper" - the wording says the same.
        if sent["jobs"]:
            voice.announce("print_sent")

    # ------------------------------------------------------------ dashboard
    def _build_dashboard(self) -> QWidget:
        page, layout = self._page()
        quick = primary_button("Quick payment  (Ctrl+J)")
        quick.clicked.connect(lambda: self._quick_payment())
        self._header(layout, "Dashboard", "Everything owed and paid across all customers", quick)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {
            "customers": KpiCard("Total customers", "users"),
            "udhaar": KpiCard(f"Total {UDHAAR_WORD}", "debit", "bar_udhaar"),
            "jama": KpiCard(f"Total {JAMA_WORD}", "credit", "bar_jama"),
            "net": KpiCard("Net outstanding", "net"),
            "today": KpiCard("Today's transactions", "today"),
            "historical": KpiCard("Historical transactions", "history"),
        }
        for i, card in enumerate(self.cards.values()):
            grid.addWidget(card, i // 3, i % 3)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        chart_card = Card(f"{UDHAAR_LABEL} vs {JAMA_WORD} – last 12 months")
        self.chart = MonthlyChart()
        chart_card.body.addWidget(self.chart, 1)
        middle.addWidget(chart_card, 3)

        side = QVBoxLayout()
        side.setSpacing(14)
        debtors = Card("Top debtors")
        self.debtor_table = make_table(["Customer", "Balance"])
        fit_columns(self.debtor_table, 0)
        self.debtor_table.setMinimumHeight(170)
        debtors.body.addWidget(self.debtor_table, 1)
        attention = Card("Needs attention")
        self.attention_label = QLabel("")
        self.attention_label.setWordWrap(True)
        attention.body.addWidget(self.attention_label)
        open_recovery = plain_button("Open Recovery")
        open_recovery.clicked.connect(lambda: self.show_page("recovery"))
        attention.body.addWidget(open_recovery, 0, Qt.AlignmentFlag.AlignLeft)
        side.addWidget(debtors, 3)
        side.addWidget(attention, 2)
        middle.addLayout(side, 2)
        layout.addLayout(middle, 1)
        return page

    def _refresh_dashboard(self) -> None:
        d = self.svc.dashboard()
        self.cards["customers"].set(str(d.total_customers), f"{d.total_transactions} entries in total")
        self.cards["udhaar"].set(money.format_inr(d.total_udhaar), "given on credit, all time")
        self.cards["jama"].set(money.format_inr(d.total_jama), "received, all time")
        net = d.net_outstanding
        self.cards["net"].set(money.format_inr(net),
                              "customers owe you" if net > 0 else ("advances held" if net < 0 else "all settled"),
                              "danger" if net > 0 else ("success" if net < 0 else ""))
        self.cards["today"].set(str(d.todays_transactions), "entries dated today")
        self.cards["historical"].set(str(d.historical_transactions), "entries dated before today")

        self.chart.set_data(insights.monthly_summary(self.svc, 12))

        debtors = insights.top_debtors(self.svc, 6)
        self.debtor_table.setRowCount(len(debtors))
        for row, s in enumerate(debtors):
            set_cell(self.debtor_table, row, 0, s.customer.name, data=s.customer.id)
            set_cell(self.debtor_table, row, 1, money.format_inr(s.balance), right=True,
                     color=balance_color(s.balance), bold=True)

        overdue = insights.overdue_items(self.svc)
        due_soon = insights.due_soon_items(self.svc, 7)
        over_limit = insights.over_limit_customers(self.svc)
        lines = []
        if overdue:
            lines.append(f"• {len(overdue)} overdue payment{'s' if len(overdue) != 1 else ''}, "
                         f"{money.format_inr(sum(r.remaining for r in overdue))} unpaid")
        if due_soon:
            lines.append(f"• {len(due_soon)} due within 7 days, {money.format_inr(sum(r.remaining for r in due_soon))}")
        if over_limit:
            lines.append(f"• {len(over_limit)} customer{'s' if len(over_limit) != 1 else ''} over credit limit")
        self.attention_label.setText("\n".join(lines) if lines else "Nothing needs attention right now.")

    # ------------------------------------------------------------ customers
    def _build_customers(self) -> QWidget:
        page, layout = self._page()
        add = primary_button("+ Add customer")
        add.clicked.connect(self._add_customer)
        cust_print = plain_button("Print")
        cust_print.setToolTip("Print the customer list with balances (respects the search box)")
        cust_print.clicked.connect(self._print_customers)
        self._header(layout, "Customers", "Each customer has a separate ledger", cust_print, add)

        self.cust_search = QLineEdit()
        self.cust_search.setPlaceholderText("Search by name or phone…")
        self.cust_search.setClearButtonEnabled(True)
        self.cust_search.textChanged.connect(lambda _t: self._refresh_customers())
        layout.addWidget(self.cust_search)

        actions = []
        for text, handler, danger in (("Quick payment", self._quick_payment_for_selected, False),
                                      ("Edit", self._edit_customer, False), ("Open ledger", self._open_customer_ledger, False),
                                      ("Add entry", self._entry_for_customer, False),
                                      ("Reminder", self._customer_reminder, False),
                                      ("STATEMENT_MENU", None, False),
                                      ("Delete", self._delete_customer, True)):
            if text == "STATEMENT_MENU":
                actions.extend(self._statement_buttons(lambda: selected_id(self.cust_table)))
                continue
            button = plain_button(text, danger=danger)
            button.clicked.connect(handler)
            actions.append(button)
        layout.addLayout(button_grid(actions, 5))

        self.cust_table = make_table(["Name", "Mobile", "Address", f"Total {UDHAAR_LABEL}", f"Total {JAMA_LABEL}", "Balance", "Credit limit"])
        fit_columns(self.cust_table, 2)
        self.cust_table.doubleClicked.connect(lambda _i: self._open_customer_ledger())
        layout.addWidget(self.cust_table, 1)
        self.cust_total = QLabel("")
        self.cust_total.setObjectName("h2")
        self.cust_total.setWordWrap(True)
        layout.addWidget(self.cust_total)
        return page

    def _refresh_customers(self, summaries=None) -> None:
        all_summaries = summaries if summaries is not None else self.svc.list_customers()
        needle = self.cust_search.text()
        shown = self.svc.list_customers(needle) if needle.strip() else all_summaries
        keep = selected_id(self.cust_table)
        warn_color = theme.current()["warning"]
        self.cust_table.setRowCount(len(shown))
        for r, s in enumerate(shown):
            c = s.customer
            state, _fraction = viewmodel.credit_state(s)
            set_cell(self.cust_table, r, 0, c.name, data=c.id, bold=True)
            set_cell(self.cust_table, r, 1, c.mobile)
            set_cell(self.cust_table, r, 2, c.address.replace("\n", " "))
            set_cell(self.cust_table, r, 3, money.format_inr(s.total_udhaar), right=True)
            set_cell(self.cust_table, r, 4, money.format_inr(s.total_jama), right=True)
            set_cell(self.cust_table, r, 5, money.format_inr(s.balance), right=True,
                     color=balance_color(s.balance), bold=True)
            set_cell(self.cust_table, r, 6, viewmodel.credit_text(s),
                     color={"over": theme.current()["danger"], "warn": warn_color}.get(state))
            if keep == c.id:
                self.cust_table.selectRow(r)
        tu = sum(s.total_udhaar for s in all_summaries)
        tj = sum(s.total_jama for s in all_summaries)
        self.cust_total.setText(
            f"Overall ({len(all_summaries)} customers):   {UDHAAR_LABEL} {money.format_inr(tu)}   "
            f"{JAMA_LABEL} {money.format_inr(tj)}   Balance {money.format_inr(tu - tj)}"
        )

    def _current_customer_id(self) -> int | None:
        cid = selected_id(self.cust_table)
        if cid is None:
            self.info("Select a customer", "Click a customer row first.")
        return cid

    def _add_customer(self) -> None:
        dialog = CustomerDialog(self.svc, parent=self)
        if dialog.exec():
            if dialog.saved:
                voice.announce("customer_added", customer=dialog.saved.name)
            self.refresh_all()

    def _edit_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        dialog = CustomerDialog(self.svc, self.svc.get_customer(cid), parent=self)
        if dialog.exec():
            if dialog.saved:
                voice.announce("customer_updated", customer=dialog.saved.name)
            self.refresh_all()

    def _delete_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        customer = self.svc.get_customer(cid)
        if not self.confirm("Delete customer", f"Delete customer '{customer.name}'?\n"
                            "Only customers with no ledger entries can be deleted."):
            return
        try:
            self.svc.delete_customer(cid)
        except KhataError as exc:
            self.warn("Cannot delete", str(exc))
            return
        voice.announce("customer_deleted")
        self.refresh_all()

    def _open_customer_ledger(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        self.ledger_customer.setCurrentIndex(max(0, self.ledger_customer.findData(cid)))
        self._refresh_ledger()
        self.show_page("ledger")

    def _entry_for_customer(self) -> None:
        cid = self._current_customer_id()
        if cid is None:
            return
        self.entry_form.select_customer(cid)
        self.show_page("entry")
        self.entry_form.item.setFocus()

    def _customer_reminder(self) -> None:
        cid = self._current_customer_id()
        if cid is not None:
            self._show_reminder(cid)

    def _show_reminder(self, customer_id: int) -> None:
        try:
            dialog = ReminderDialog(self.svc, customer_id, parent=self)
        except KhataError as exc:  # e.g. the customer owes nothing
            self.info("No reminder needed", str(exc))
            return
        dialog.exec()

    # ------------------------------------------------------------ daily entry
    def _build_daily(self) -> QWidget:
        page, layout = self._page()
        self._header(layout, "Daily Entry", "Many products and many customers in one go – saved into the "
                     "ledgers automatically after 24 hours")
        self.daily_page = DailyEntryPage(self.svc, on_saved=self.refresh_all, info=self.info, warn=self.warn,
                                         confirm=self.confirm, new_customer=self._daily_new_customer)
        layout.addWidget(self.daily_page, 1)
        return page

    def _daily_new_customer(self, name: str = "") -> int | None:
        """'+ New customer' on the Daily Entry page (``name`` pre-fills the box). Returns the new customer's id."""
        dialog = CustomerDialog(self.svc, parent=self, name=name)
        if dialog.exec() and dialog.saved:
            self.refresh_all()
            return dialog.saved.id
        return None

    # ------------------------------------------------------------ new entry
    def _build_entry(self) -> QWidget:
        page, layout = self._page()
        quick = primary_button("Quick payment  (Ctrl+J)")
        quick.clicked.connect(lambda: self._quick_payment())
        statement = self._statement_buttons(self._entry_customer_id_or_none)
        self._header(layout, "New Entry", f"Record {UDHAAR_WORD} or {JAMA_WORD} – any date, including old dates. "
                     "Only a payment? Use Quick payment – just the amount.", *statement, quick)
        card = Card()
        self.entry_form = EntryForm(with_customer=True)
        self.entry_form.new_customer_button.clicked.connect(self._quick_new_customer)
        self.entry_form.item.returnPressed.connect(self._save_entry)
        self.entry_form.amount.returnPressed.connect(self._save_entry)
        self.entry_form.customer.currentIndexChanged.connect(self._update_entry_context)
        self.entry_form.customer.editTextChanged.connect(self._update_entry_context)
        card.body.addWidget(self.entry_form)
        self.entry_context = QLabel("")
        self.entry_context.setObjectName("muted")
        self.entry_context.setWordWrap(True)
        card.body.addWidget(self.entry_context)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        clear = plain_button("Clear")
        clear.clicked.connect(self.entry_form.clear_for_next)
        self.entry_save = primary_button("Save entry")
        self.entry_save.setToolTip("After saving, customer, type and date stay selected so the next item "
                                   "can be typed straight away")
        self.entry_save.clicked.connect(self._save_entry)
        buttons.addWidget(clear)
        buttons.addWidget(self.entry_save)
        card.body.addLayout(buttons)
        layout.addWidget(card)

        recent = Card("Saved in this session")
        self.recent_table = make_table(["Customer", "Date", "Item / Description", "Type", "Amount", "Saved at"])
        fit_columns(self.recent_table, 2)
        self.recent_table.setToolTip("Double-click a row to print its receipt (thermal slip)")
        self.recent_table.doubleClicked.connect(self._receipt_from_recent)
        recent.body.addWidget(self.recent_table, 1)
        layout.addWidget(recent, 1)
        return page

    # ------------------------------------------------------------ quick payment
    def _quick_payment(self, customer_id: int | None = None) -> None:
        """Open the amount-only payment dialog (no item needed)."""
        if not self.svc.list_customers():
            self.info("No customers", "Add a customer first.")
            return
        dialog = QuickPaymentDialog(self.svc, self._add_with_prompts, customer_id=customer_id, parent=self)
        if dialog.exec() and dialog.saved:
            txn = dialog.saved
            customer = self.svc.get_customer(txn.customer_id)
            balance = self.svc.customer_balance(txn.customer_id)
            voice.announce("payment_received", amount=txn.amount_paise, balance=balance,
                           customer=customer.name)
            self.refresh_all()
            self.statusBar().showMessage(
                f"{money.format_inr(txn.amount_paise)} received from {customer.name}. "
                f"New balance: {money.format_inr(balance)}", 8000)

    def _quick_payment_for_selected(self) -> None:
        self._quick_payment(selected_id(self.cust_table))

    def _ledger_quick_payment(self) -> None:
        self._quick_payment(self.ledger_customer.currentData())

    def _update_entry_context(self, *_args) -> None:
        try:
            cid = self.entry_form.customer_id()
            customer = self.svc.get_customer(cid)
        except KhataError:
            self.entry_context.setText("")
            return
        balance = self.svc.customer_balance(cid)
        text = f"Current balance: {money.format_inr(balance)}"
        if customer.credit_limit_paise:
            used = round(max(0, balance) * 100 / customer.credit_limit_paise)
            text += f"   |   Credit limit {money.format_inr(customer.credit_limit_paise)} ({used}% used)"
        self.entry_context.setText(text)

    def _quick_new_customer(self) -> None:
        dialog = CustomerDialog(self.svc, parent=self)
        if dialog.exec() and dialog.saved:
            voice.announce("customer_added", customer=dialog.saved.name)
            self.refresh_all()
            self.entry_form.select_customer(dialog.saved.id)

    def _add_with_prompts(self, customer_id: int, values: dict):
        """Save an entry; ask before overriding the duplicate guard or the customer's credit limit."""
        allow_duplicate = False
        allow_over_limit = False
        over_balance = 0
        while True:
            try:
                txn = self.svc.add_transaction(customer_id, allow_duplicate=allow_duplicate,
                                               allow_over_limit=allow_over_limit, **values)
            except DuplicateEntryError as exc:
                if allow_duplicate or not self.confirm("Possible duplicate", f"{exc}\n\nSave it again anyway?"):
                    return None
                allow_duplicate = True
                continue
            except CreditLimitError as exc:
                if allow_over_limit or not self.confirm("Credit limit exceeded", f"{exc}\n\nSave this entry anyway?"):
                    return None
                allow_over_limit = True
                over_balance = exc.new_balance_paise   # the exact figure the box on screen just showed
                continue
            # only after the row is really in the ledger, so a warning is never spoken about an entry that was not kept
            if allow_over_limit:
                voice.announce("entry_over_limit", customer=self.svc.get_customer(customer_id).name,
                               balance=over_balance)
            elif allow_duplicate:
                voice.announce("entry_duplicate")
            return txn

    def _save_entry(self) -> None:
        if self._saving:
            return
        self._saving = True
        self.entry_save.setEnabled(False)
        try:
            try:
                cid = self.entry_form.customer_id()
                txn = self._add_with_prompts(cid, self.entry_form.values())
            except KhataError as exc:
                self.warn("Cannot save entry", str(exc))
                return
            if txn is None:
                return
            customer = self.svc.get_customer(cid)
            self.recent_table.insertRow(0)
            set_cell(self.recent_table, 0, 0, customer.name, data=txn.id)
            set_cell(self.recent_table, 0, 1, dates.format_date(txn.txn_date))
            set_cell(self.recent_table, 0, 2, txn.item)
            set_cell(self.recent_table, 0, 3, type_label(txn.txn_type))
            set_cell(self.recent_table, 0, 4, money.format_inr(txn.amount_paise), right=True,
                     color=balance_color(txn.amount_paise if txn.txn_type == "UDHAAR" else -txn.amount_paise))
            set_cell(self.recent_table, 0, 5, datetime.fromisoformat(txn.created_at).strftime("%H:%M:%S"))
            self.entry_form.clear_for_next()
            voice.announce("entry_added", type=txn.txn_type, amount=txn.amount_paise, customer=customer.name)
            self.refresh_all()
            self.statusBar().showMessage("Entry saved", 4000)
        finally:
            self._saving = False
            self.entry_save.setEnabled(True)

    # ------------------------------------------------------------ ledger
    def _build_ledger(self) -> QWidget:
        page, layout = self._page()
        print_btn = plain_button("Print")
        pdf = plain_button("PDF")
        xlsx = plain_button("Excel")
        print_btn.clicked.connect(lambda: self._export_statement("print"))
        pdf.clicked.connect(lambda: self._export_statement("pdf"))
        xlsx.clicked.connect(lambda: self._export_statement("xlsx"))
        self._header(layout, "Ledger", "Chronological ledger with running balance", print_btn, pdf, xlsx)

        filters = QHBoxLayout()
        self.ledger_customer = QComboBox()
        self.ledger_customer.setMinimumWidth(260)
        self.ledger_range = QCheckBox("Date range")
        self.ledger_from = DateField()
        self.ledger_to = DateField()
        self.ledger_from.setEnabled(False)
        self.ledger_to.setEnabled(False)
        self.ledger_search = QLineEdit()
        self.ledger_search.setPlaceholderText("Search item description…")
        self.ledger_search.setClearButtonEnabled(True)
        filters.addWidget(QLabel("Customer"))
        filters.addWidget(self.ledger_customer)
        filters.addWidget(self.ledger_range)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.ledger_from)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self.ledger_to)
        filters.addStretch(1)
        layout.addLayout(filters)
        layout.addWidget(self.ledger_search)
        self.ledger_customer.currentIndexChanged.connect(lambda _i: self._refresh_ledger())
        self.ledger_range.toggled.connect(self._ledger_range_toggled)
        self.ledger_from.dateChanged.connect(lambda _d: self._refresh_ledger())
        self.ledger_to.dateChanged.connect(lambda _d: self._refresh_ledger())
        self.ledger_search.textChanged.connect(lambda _t: self._refresh_ledger())

        credit_row = QHBoxLayout()
        self.ledger_info = QLabel("")
        self.ledger_info.setObjectName("h2")
        self.ledger_info.setWordWrap(True)
        self.ledger_credit_label = QLabel("")
        self.ledger_credit_label.setObjectName("muted")
        self.ledger_credit_label.setWordWrap(True)
        self.ledger_credit_bar = QProgressBar()
        self.ledger_credit_bar.setRange(0, 100)
        self.ledger_credit_bar.setFixedWidth(180)
        self.ledger_credit_bar.setTextVisible(False)
        credit_row.addWidget(self.ledger_info, 1)
        credit_row.addWidget(self.ledger_credit_label)
        credit_row.addWidget(self.ledger_credit_bar)
        layout.addLayout(credit_row)

        self.ledger_table = make_table(["Date", "Item / Description", "Qty", "Rate", UDHAAR_LABEL, JAMA_LABEL, "Balance",
                                        "Due", "Notes"])
        fit_columns(self.ledger_table, 1)
        self.ledger_table.doubleClicked.connect(lambda _i: self._edit_entry())
        layout.addWidget(self.ledger_table, 1)

        self.ledger_summary = QLabel("")
        self.ledger_summary.setObjectName("h2")
        self.ledger_summary.setWordWrap(True)
        self.ledger_note = QLabel("")
        self.ledger_note.setObjectName("muted")
        self.ledger_note.setWordWrap(True)
        layout.addWidget(self.ledger_summary)
        layout.addWidget(self.ledger_note)

        add = primary_button("+ Add entry")
        quick_pay = plain_button("Quick payment")
        quick_pay.clicked.connect(self._ledger_quick_payment)
        edit = plain_button("Edit selected")
        delete = plain_button("Delete selected", danger=True)
        remind = plain_button("Send reminder")
        slip = plain_button("Print receipt")
        slip.setToolTip("Print a small thermal slip (58/80 mm) for the selected entry.\n"
                        "The receipt printer is chosen separately from the statement printer under Settings.")
        add.clicked.connect(self._ledger_add_entry)
        edit.clicked.connect(self._edit_entry)
        delete.clicked.connect(self._delete_entry)
        remind.clicked.connect(self._ledger_reminder)
        slip.clicked.connect(self._ledger_receipt)
        layout.addLayout(button_grid((add, quick_pay, edit, delete, slip, remind), 3))
        return page

    def _ledger_range_toggled(self, on: bool) -> None:
        self.ledger_from.setEnabled(on)
        self.ledger_to.setEnabled(on)
        self._refresh_ledger()

    def _ledger_args(self) -> dict:
        args = {"search": self.ledger_search.text()}
        if self.ledger_range.isChecked():
            args["date_from"] = self.ledger_from.text_dmy()
            args["date_to"] = self.ledger_to.text_dmy()
        return args

    def _current_ledger(self):
        cid = self.ledger_customer.currentData()
        if cid is None:
            return None
        return self.svc.ledger(cid, **self._ledger_args())

    def _refresh_ledger(self) -> None:
        self.ledger_table.setRowCount(0)
        self.ledger_credit_bar.setVisible(False)
        self.ledger_credit_label.setText("")
        try:
            led = self._current_ledger()
        except KhataError as exc:
            self.ledger_summary.setText("")
            self.ledger_info.setText("")
            self.ledger_note.setText(str(exc))
            return
        if led is None:
            self.ledger_info.setText("")
            self.ledger_summary.setText("Add a customer to begin.")
            self.ledger_note.setText("")
            return

        customer = led.customer
        self.ledger_info.setText(customer.name + (f"   ·   {customer.mobile}" if customer.mobile else ""))
        if customer.credit_limit_paise:
            balance = self.svc.customer_balance(customer.id)
            fraction = max(0.0, balance / customer.credit_limit_paise)
            self.ledger_credit_bar.setValue(min(100, round(fraction * 100)))
            _set_state(self.ledger_credit_bar, "over" if fraction > 1 else ("warn" if fraction >= viewmodel.WARN_FRACTION else ""))
            self.ledger_credit_label.setText(f"Credit used {money.format_inr(max(0, balance))} of "
                                             f"{money.format_inr(customer.credit_limit_paise)}")
            self.ledger_credit_bar.setVisible(True)

        open_list, _advance = insights.open_items(self.svc, customer.id)
        today_iso = self.svc.today().isoformat()
        overdue_ids = {i.txn.id for i in open_list if i.txn.due_date and i.txn.due_date < today_iso}
        danger = theme.current()["danger"]
        success = theme.current()["success"]

        self.ledger_table.setRowCount(len(led.rows))
        for r, row in enumerate(led.rows):
            t = row.txn
            set_cell(self.ledger_table, r, 0, dates.format_date(t.txn_date), data=t.id)
            set_cell(self.ledger_table, r, 1, t.item)
            set_cell(self.ledger_table, r, 2, t.quantity or "", right=True)
            set_cell(self.ledger_table, r, 3, money.format_inr(t.rate_paise, symbol=False) if t.rate_paise else "", right=True)
            set_cell(self.ledger_table, r, 4, money.format_inr(row.udhaar, symbol=False) if row.udhaar else "",
                     right=True, color=danger if row.udhaar else None)
            set_cell(self.ledger_table, r, 5, money.format_inr(row.jama, symbol=False) if row.jama else "",
                     right=True, color=success if row.jama else None)
            set_cell(self.ledger_table, r, 6, money.format_inr(row.balance, symbol=False), right=True,
                     color=balance_color(row.balance), bold=True)
            if t.due_date:
                overdue = t.id in overdue_ids
                set_cell(self.ledger_table, r, 7, dates.format_date(t.due_date) + ("  (overdue)" if overdue else ""),
                         color=danger if overdue else None)
            else:
                set_cell(self.ledger_table, r, 7, "")
            set_cell(self.ledger_table, r, 8, t.notes.replace("\n", " "))
        self.ledger_summary.setText(
            f"Opening {money.format_inr(led.opening_balance)}   |   "
            f"{UDHAAR_LABEL} {money.format_inr(led.total_udhaar)}   {JAMA_LABEL} {money.format_inr(led.total_jama)}   "
            f"({len(led.rows)} entries)   |   Closing {money.format_inr(led.closing_balance)}"
        )
        note = "Sorted by transaction date; entries on the same date keep the order they were entered."
        self.ledger_table.setToolTip(note)
        self.ledger_note.setText("Item search is active: the running balance still shows the true account balance."
                                 if led.search else "")

    def _ledger_add_entry(self) -> None:
        cid = self.ledger_customer.currentData()
        if cid is not None:
            self.entry_form.select_customer(cid)
        self.show_page("entry")

    def _ledger_reminder(self) -> None:
        cid = self.ledger_customer.currentData()
        if cid is None:
            self.info("No customer", "Select a customer first.")
            return
        self._show_reminder(cid)

    def _selected_txn_id(self) -> int | None:
        tid = selected_id(self.ledger_table)
        if tid is None:
            self.info("Select an entry", "Click an entry row first.")
        return tid

    def _edit_entry(self) -> None:
        tid = self._selected_txn_id()
        if tid is None:
            return
        txn = self.svc.get_transaction(tid)
        customer = self.svc.get_customer(txn.customer_id)
        dialog = EditEntryDialog(self.svc, txn, customer.name, parent=self)
        if dialog.exec():
            if dialog.saved:
                voice.announce("entry_updated", amount=dialog.saved.amount_paise)
            self.refresh_all()

    def _delete_entry(self) -> None:
        tid = self._selected_txn_id()
        if tid is None:
            return
        txn = self.svc.get_transaction(tid)
        text = (f"Delete this entry?\n\n{dates.format_date(txn.txn_date)}  {type_label(txn.txn_type)}  "
                f"{money.format_inr(txn.amount_paise)}\n{txn.item}\n\nThis cannot be undone (it is kept in the "
                "Activity log). All balances will be recalculated.")
        if not self.confirm("Delete entry", text):
            return
        try:
            self.svc.delete_transaction(tid)
        except KhataError as exc:
            self.warn("Cannot delete", str(exc))
            return
        voice.announce("entry_deleted")
        self.refresh_all()

    def _entry_customer_id_or_none(self) -> int | None:
        try:
            return self.entry_form.customer_id()
        except KhataError:
            return None

    def _statement_buttons(self, get_customer_id) -> list[QPushButton]:
        """Three one-click buttons (Print / PDF / Excel) for whichever customer ``get_customer_id()`` returns."""
        buttons = []
        for text, kind, tip in (("Print", "print", "Print this customer's statement"),
                                ("PDF", "pdf", "Save this customer's statement as a PDF file"),
                                ("Excel", "xlsx", "Save this customer's statement as an Excel file")):
            button = plain_button(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, k=kind: self._customer_statement(get_customer_id, k))
            buttons.append(button)
        return buttons

    def _customer_statement(self, get_customer_id, kind: str) -> None:
        """Statement of one customer (whole history) for the menu buttons on the Customers / New Entry pages."""
        cid = get_customer_id()
        if cid is None:
            self.info("Select a customer", "Select a customer first.")
            return
        try:
            led = self.svc.ledger(cid)
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        self._statement_output(led, kind)

    def _export_statement(self, kind: str) -> None:
        """Statement of the customer shown on the Ledger page (respects its date range and search)."""
        try:
            led = self._current_ledger()
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if led is None:
            self.info("No customer", "Select a customer first.")
            return
        self._statement_output(led, kind)

    def _statement_output(self, led, kind: str) -> None:
        if kind == "print":
            self._print_statement(led)
            return
        period = ""
        if led.date_from or led.date_to:
            period = f"_{led.date_from or 'start'}_{led.date_to or 'latest'}"
        default = f"Statement_{_safe_filename(led.customer.name)}{period}.{kind}"
        fn = exports.export_statement_pdf if kind == "pdf" else exports.export_statement_xlsx
        self._run_export("Export statement", default, kind,
                         lambda path: fn(led, path, self.shop_name(), self.shop_details()))

    def _ask_page_setup(self) -> bool:
        """Show the Page setup dialog (all paper sizes, custom size, portrait / landscape). False = cancelled."""
        dlg = PageSetupDialog(pagesetup.load(self.svc), self)
        if not dlg.exec():
            return False
        pagesetup.save(self.svc, dlg.result_setup)
        return True

    @staticmethod
    def _printer_for(setup):
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if setup.is_custom:
            size = QPageSize(QSizeF(setup.custom_w_mm, setup.custom_h_mm), QPageSize.Unit.Millimeter, "Custom")
            smallest = min(setup.custom_w_mm, setup.custom_h_mm)
        else:
            try:
                size = QPageSize(QPageSize.PageSizeId[setup.size])
            except KeyError:  # a paper size name this Qt version does not know: fall back to A4
                size = QPageSize(QPageSize.PageSizeId.A4)
            smallest = min(size.size(QPageSize.Unit.Millimeter).width(), size.size(QPageSize.Unit.Millimeter).height())
        printer.setPageSize(size)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape if setup.orientation == pagesetup.LANDSCAPE
                                   else QPageLayout.Orientation.Portrait)
        margin = max(3.0, min(12.0, smallest * 0.05))  # small pages get smaller margins
        printer.setPageMargins(QMarginsF(margin, margin, margin, margin), QPageLayout.Unit.Millimeter)
        return printer

    def _print_statement(self, led) -> None:
        """Page setup, then print preview with printer choice; works with any printer or 'Microsoft Print to PDF'."""
        def build_html() -> str:
            return exports.statement_html(led, self.shop_name(), self.shop_details(),
                                          items=frozenset(pagesetup.load(self.svc).items))

        self._preview_and_print(f"Print statement \u2013 {led.customer.name}", build_html)

    # ------------------------------------------------------------ receipt (thermal slip, 58/80 mm)
    def _ledger_receipt(self) -> None:
        tid = self._selected_txn_id()
        if tid is not None:
            self._print_receipt_for_txn(tid)

    def _receipt_from_recent(self, index) -> None:
        item = self.recent_table.item(index.row(), 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is not None:
            self._print_receipt_for_txn(int(tid))

    def _print_receipt_for_txn(self, txn_id: int) -> None:
        try:
            txn = self.svc.get_transaction(txn_id)
            customer = self.svc.get_customer(txn.customer_id)
        except KhataError as exc:
            self.warn("Receipt", str(exc))
            return
        setup = receipt.load(self.svc)
        balance = self.svc.customer_balance(customer.id) if setup.show_balance else None
        html_text = receipt.build_receipt_html(self.shop_name(), self.shop_details(), customer, txn,
                                               width_mm=setup.width_mm, balance_paise=balance,
                                               print_time=datetime.now().strftime("%d-%m-%Y %H:%M"))
        self._send_receipt(html_text, setup)

    def _send_receipt(self, html_text: str, setup) -> None:
        """Print straight to the receipt printer (no preview - thermal slips are one short page).

        The page is custom-sized: roll width x the measured height of the content, so the printer cuts
        after the slip instead of feeding a whole A4 sheet.
        """
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        asked = False
        if setup.printer:
            names = {p.printerName() for p in QPrinterInfo.availablePrinters()}
            if setup.printer in names:
                printer.setPrinterName(setup.printer)
            elif not self.confirm("Receipt printer",
                                  f"The receipt printer '{setup.printer}' is not available right now.\n\n"
                                  "Choose a printer for this slip instead?"):
                return
        if not printer.printerName():
            dialog = QPrintDialog(printer, self)
            dialog.setWindowTitle("Print receipt")
            if not dialog.exec():
                return
            asked = True
        try:
            doc = QTextDocument()
            doc.setHtml(html_text)
            px_per_mm = 96 / 25.4
            doc.setPageSize(QSizeF((setup.width_mm - 4) * px_per_mm, 4000 * px_per_mm))
            height_mm = max(25.0, doc.size().height() / px_per_mm + 6)
            size = QPageSize(QSizeF(float(setup.width_mm), height_mm), QPageSize.Unit.Millimeter, "Receipt")
            printer.setPageSize(size)
            printer.setPageMargins(QMarginsF(1, 1, 1, 1), QPageLayout.Unit.Millimeter)
            doc.setPageSize(QSizeF((setup.width_mm - 6) * px_per_mm, (height_mm - 4) * px_per_mm))
            (getattr(doc, "print_", None) or doc.print)(printer)
        except Exception as exc:  # noqa: BLE001 - a printer problem must never crash the app
            voice.announce("print_failed")
            self.warn("Receipt", f"Printing failed:\n{exc}")
            return
        voice.announce("print_sent")
        if not asked and setup.printer:
            self.statusBar().showMessage(f"Receipt sent to {setup.printer}", 6000)

    # ------------------------------------------------------------ recovery (aging / overdue / limits)
    def _build_recovery(self) -> QWidget:
        page, layout = self._page()
        self.rec_asof = DateField(allow_future=True)
        self.rec_asof.dateChanged.connect(lambda _d: self._refresh_recovery())
        asof_box = QWidget()
        asof_row = QHBoxLayout(asof_box)
        asof_row.setContentsMargins(0, 0, 0, 0)
        asof_row.addWidget(QLabel("As on"))
        asof_row.addWidget(self.rec_asof)
        self._header(layout, "Recovery", "Who owes what, for how long, and who to remind", asof_box)

        self.rec_tabs = QTabWidget()

        # -- aging
        aging_tab = QWidget()
        aging_layout = QVBoxLayout(aging_tab)
        aging_layout.setContentsMargins(0, 12, 0, 0)
        self.aging_table = make_table(["Customer", "Mobile", "0-30 days", "31-60 days", "61-90 days", "90+ days",
                                       "Outstanding", "Oldest unpaid"])
        fit_columns(self.aging_table, 0)
        aging_layout.addWidget(self.aging_table, 1)
        self.aging_total = QLabel("")
        self.aging_total.setObjectName("h2")
        self.aging_total.setWordWrap(True)
        aging_layout.addWidget(self.aging_total)
        self.aging_table.setToolTip("Payments are applied to the oldest unpaid debit (Dr) first (FIFO). "
                                    "Advance payments offset the customer's next debit (Dr).")
        aging_buttons = QHBoxLayout()
        remind = primary_button("Send reminder")
        remind.clicked.connect(lambda: self._recovery_reminder(self.aging_table))
        aging_print = plain_button("Print")
        aging_print.clicked.connect(self._print_aging)
        aging_pdf = plain_button("Export PDF")
        aging_xlsx = plain_button("Export Excel")
        aging_pdf.clicked.connect(lambda: self._export_aging("pdf"))
        aging_xlsx.clicked.connect(lambda: self._export_aging("xlsx"))
        aging_buttons.addWidget(remind)
        aging_buttons.addStretch(1)
        aging_buttons.addWidget(aging_print)
        aging_buttons.addWidget(aging_pdf)
        aging_buttons.addWidget(aging_xlsx)
        aging_layout.addLayout(aging_buttons)
        self.rec_tabs.addTab(aging_tab, "Aging")

        # -- overdue
        overdue_tab = QWidget()
        overdue_layout = QVBoxLayout(overdue_tab)
        overdue_layout.setContentsMargins(0, 12, 0, 0)
        self.overdue_table = make_table(["Customer", "Item / Description", "Entry date", "Due date", "Days overdue",
                                         "Unpaid"])
        fit_columns(self.overdue_table, 1)
        overdue_layout.addWidget(self.overdue_table, 1)
        self.overdue_total = QLabel("")
        self.overdue_total.setObjectName("h2")
        self.overdue_total.setWordWrap(True)
        overdue_layout.addWidget(self.overdue_total)
        overdue_buttons = QHBoxLayout()
        remind2 = primary_button("Send reminder")
        remind2.clicked.connect(lambda: self._recovery_reminder(self.overdue_table))
        overdue_print = plain_button("Print")
        overdue_print.clicked.connect(self._print_overdue)
        overdue_pdf = plain_button("Export PDF")
        overdue_xlsx = plain_button("Export Excel")
        overdue_pdf.clicked.connect(lambda: self._export_overdue("pdf"))
        overdue_xlsx.clicked.connect(lambda: self._export_overdue("xlsx"))
        overdue_buttons.addWidget(remind2)
        overdue_buttons.addStretch(1)
        overdue_buttons.addWidget(overdue_print)
        overdue_buttons.addWidget(overdue_pdf)
        overdue_buttons.addWidget(overdue_xlsx)
        overdue_layout.addLayout(overdue_buttons)
        self.rec_tabs.addTab(overdue_tab, "Overdue")

        # -- credit limits
        limit_tab = QWidget()
        limit_layout = QVBoxLayout(limit_tab)
        limit_layout.setContentsMargins(0, 12, 0, 0)
        self.limit_table = make_table(["Customer", "Balance", "Credit limit", "Over by", "Used"])
        fit_columns(self.limit_table, 0)
        limit_layout.addWidget(self.limit_table, 1)
        limit_buttons = QHBoxLayout()
        limit_buttons.addStretch(1)
        limit_print = plain_button("Print")
        limit_print.clicked.connect(self._print_over_limit)
        limit_buttons.addWidget(limit_print)
        limit_layout.addLayout(limit_buttons)
        self.rec_tabs.addTab(limit_tab, "Over credit limit")

        layout.addWidget(self.rec_tabs, 1)
        return page

    def _refresh_recovery(self) -> None:
        as_of = self.rec_asof.text_dmy()
        try:
            report = insights.aging_report(self.svc, as_of)
            overdue = insights.overdue_items(self.svc, as_of)
        except KhataError as exc:
            self.warn("Recovery", str(exc))
            return
        danger = theme.current()["danger"]

        self.aging_table.setRowCount(len(report.rows))
        for r, row in enumerate(report.rows):
            set_cell(self.aging_table, r, 0, row.customer.name, data=row.customer.id, bold=True)
            set_cell(self.aging_table, r, 1, row.customer.mobile)
            for i, amount in enumerate(row.buckets):
                set_cell(self.aging_table, r, 2 + i, money.format_inr(amount, symbol=False) if amount else "",
                         right=True, color=danger if (i == 3 and amount) else None)
            set_cell(self.aging_table, r, 6, money.format_inr(row.total, symbol=False), right=True, bold=True)
            set_cell(self.aging_table, r, 7, dates.format_date(row.oldest_date) if row.oldest_date else "")
        self.aging_total.setText(
            "Total outstanding " + money.format_inr(report.total_outstanding) + "   |   " +
            "   ".join(f"{label}: {money.format_inr(amount)}"
                        for label, amount in zip(insights.BUCKET_LABELS, report.bucket_totals)))

        self.overdue_table.setRowCount(len(overdue))
        for r, row in enumerate(overdue):
            set_cell(self.overdue_table, r, 0, row.customer.name, data=row.customer.id, bold=True)
            set_cell(self.overdue_table, r, 1, row.txn.item)
            set_cell(self.overdue_table, r, 2, dates.format_date(row.txn.txn_date))
            set_cell(self.overdue_table, r, 3, dates.format_date(row.txn.due_date))
            set_cell(self.overdue_table, r, 4, str(row.days_overdue), right=True, color=danger)
            set_cell(self.overdue_table, r, 5, money.format_inr(row.remaining), right=True, bold=True)
        self.overdue_total.setText(f"{len(overdue)} overdue payment{'s' if len(overdue) != 1 else ''}, "
                                   f"{money.format_inr(sum(r.remaining for r in overdue))} unpaid")

        over = insights.over_limit_customers(self.svc)
        self.limit_table.setRowCount(len(over))
        for r, s in enumerate(over):
            limit = s.customer.credit_limit_paise
            set_cell(self.limit_table, r, 0, s.customer.name, data=s.customer.id, bold=True)
            set_cell(self.limit_table, r, 1, money.format_inr(s.balance), right=True, color=danger)
            set_cell(self.limit_table, r, 2, money.format_inr(limit), right=True)
            set_cell(self.limit_table, r, 3, money.format_inr(s.balance - limit), right=True, color=danger, bold=True)
            set_cell(self.limit_table, r, 4, f"{round(s.balance * 100 / limit)}%", right=True)

    def _recovery_reminder(self, table) -> None:
        cid = selected_id(table)
        if cid is None:
            self.info("Select a customer", "Click a row first.")
            return
        self._show_reminder(cid)

    def _export_aging(self, kind: str) -> None:
        try:
            report = insights.aging_report(self.svc, self.rec_asof.text_dmy())
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if not report.rows:
            self.info("Nothing to export", "No customer has an outstanding balance.")
            return
        fn = exports.export_aging_pdf if kind == "pdf" else exports.export_aging_xlsx
        self._run_export("Export aging report", f"Aging_{report.as_of}.{kind}", kind,
                         lambda path: fn(report, path, self.shop_name()))

    def _export_overdue(self, kind: str) -> None:
        try:
            as_of_iso = dates.parse_date(self.rec_asof.text_dmy(), allow_future=True)
            rows = insights.overdue_items(self.svc, as_of_iso)
        except (KhataError, dates.DateError) as exc:
            self.warn("Cannot export", str(exc))
            return
        if not rows:
            self.info("Nothing to export", "There are no overdue payments.")
            return
        fn = exports.export_overdue_pdf if kind == "pdf" else exports.export_overdue_xlsx
        self._run_export("Export overdue payments", f"Overdue_{as_of_iso}.{kind}", kind,
                         lambda path: fn(rows, as_of_iso, path, self.shop_name()))

    # ------------------------------------------------------------ print: every report
    def _print_customers(self) -> None:
        text = self.cust_search.text()
        summaries = self.svc.list_customers(text)
        if not summaries:
            self.info("Nothing to print", "No customers match this search.")
            return
        self.print_html(exports.customers_html(summaries, self.shop_name(), text.strip()), "Print customers")

    def _print_aging(self) -> None:
        try:
            report = insights.aging_report(self.svc, self.rec_asof.text_dmy())
        except KhataError as exc:
            self.warn("Cannot print", str(exc))
            return
        if not report.rows:
            self.info("Nothing to print", "No customer has an outstanding balance.")
            return
        self.print_html(exports.aging_html(report, self.shop_name()), "Print aging report")

    def _print_overdue(self) -> None:
        try:
            as_of_iso = dates.parse_date(self.rec_asof.text_dmy(), allow_future=True)
            rows = insights.overdue_items(self.svc, as_of_iso)
        except (KhataError, dates.DateError) as exc:
            self.warn("Cannot print", str(exc))
            return
        if not rows:
            self.info("Nothing to print", "There are no overdue payments.")
            return
        self.print_html(exports.overdue_html(rows, as_of_iso, self.shop_name()), "Print overdue payments")

    def _print_over_limit(self) -> None:
        over = insights.over_limit_customers(self.svc)
        if not over:
            self.info("Nothing to print", "No customer is over the credit limit.")
            return
        self.print_html(exports.over_limit_html(over, self.shop_name()), "Print over credit limit")

    def _print_register(self) -> None:
        cid = self.report_customer.currentData()
        args = {"search": self.rep_search.text(), "customer_ids": None if cid is None else [cid]}
        if self.rep_range.isChecked():
            args["date_from"] = self.rep_from.text_dmy()
            args["date_to"] = self.rep_to.text_dmy()
        try:
            reg = self.svc.register(**args)
        except KhataError as exc:
            self.warn("Cannot print", str(exc))
            return
        if not reg.rows:
            self.info("Nothing to print", "No entries match these filters.")
            return
        label = self.report_customer.currentText() or "All customers"
        self.print_html(exports.register_html(reg, self.shop_name(), label), "Print register")

    def _print_balances(self) -> None:
        text = self.rep_cust_filter.text()
        summaries = self.svc.list_customers(text)
        if not summaries:
            self.info("Nothing to print", "No customers match this filter.")
            return
        self.print_html(exports.customers_html(summaries, self.shop_name(), text.strip()), "Print customer balances")

    def _print_activity(self) -> None:
        entries = self.svc.audit_entries(500, search=self.audit_search.text())
        if not entries:
            self.info("Nothing to print", "The activity log has no entries for this search.")
            return
        self.print_html(exports.activity_html(entries, self.shop_name(), self.audit_search.text().strip()),
                        "Print activity log")

    # ------------------------------------------------------------ reports
    def _build_reports(self) -> QWidget:
        page, layout = self._page()
        self._header(layout, "Reports", "Export exactly the data you select")

        register = Card("Transaction register")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.report_customer = QComboBox()
        self.rep_range = QCheckBox("Limit to date range")
        self.rep_from = DateField()
        self.rep_to = DateField()
        self.rep_from.setEnabled(False)
        self.rep_to.setEnabled(False)
        self.rep_range.toggled.connect(lambda on: (self.rep_from.setEnabled(on), self.rep_to.setEnabled(on)))
        dates_row = QHBoxLayout()
        dates_row.addWidget(self.rep_range)
        dates_row.addWidget(QLabel("From"))
        dates_row.addWidget(self.rep_from)
        dates_row.addWidget(QLabel("To"))
        dates_row.addWidget(self.rep_to)
        dates_row.addStretch(1)
        self.rep_search = QLineEdit()
        self.rep_search.setPlaceholderText("Item description contains… (optional)")
        form.addRow("Customer", self.report_customer)
        form.addRow("Dates", dates_row)
        form.addRow("Item search", self.rep_search)
        register.body.addLayout(form)
        reg_buttons = QHBoxLayout()
        reg_buttons.addStretch(1)
        reg_print = plain_button("Print")
        reg_print.clicked.connect(self._print_register)
        reg_pdf = plain_button("Register PDF")
        reg_xlsx = primary_button("Register Excel")
        reg_pdf.clicked.connect(lambda: self._export_register("pdf"))
        reg_xlsx.clicked.connect(lambda: self._export_register("xlsx"))
        reg_buttons.addWidget(reg_print)
        reg_buttons.addWidget(reg_pdf)
        reg_buttons.addWidget(reg_xlsx)
        register.body.addLayout(reg_buttons)
        layout.addWidget(register)

        balances = Card("Customer balance summary")
        bform = QFormLayout()
        self.rep_cust_filter = QLineEdit()
        self.rep_cust_filter.setPlaceholderText("Name or phone contains… (optional)")
        bform.addRow("Customers", self.rep_cust_filter)
        balances.body.addLayout(bform)
        bal_buttons = QHBoxLayout()
        bal_buttons.addStretch(1)
        bal_print = plain_button("Print")
        bal_print.clicked.connect(self._print_balances)
        bal_pdf = plain_button("Balances PDF")
        bal_xlsx = primary_button("Balances Excel")
        bal_pdf.clicked.connect(lambda: self._export_customers("pdf"))
        bal_xlsx.clicked.connect(lambda: self._export_customers("xlsx"))
        bal_buttons.addWidget(bal_print)
        bal_buttons.addWidget(bal_pdf)
        bal_buttons.addWidget(bal_xlsx)
        balances.body.addLayout(bal_buttons)
        layout.addWidget(balances)

        tip = QLabel("A single customer's statement (opening/closing balance, running balance) is exported from the "
                     "Ledger page. Aging and overdue reports are on the Recovery page.")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _export_register(self, kind: str) -> None:
        cid = self.report_customer.currentData()
        args = {"search": self.rep_search.text(), "customer_ids": None if cid is None else [cid]}
        if self.rep_range.isChecked():
            args["date_from"] = self.rep_from.text_dmy()
            args["date_to"] = self.rep_to.text_dmy()
        try:
            reg = self.svc.register(**args)
        except KhataError as exc:
            self.warn("Cannot export", str(exc))
            return
        if not reg.rows:
            self.info("Nothing to export", "No entries match these filters.")
            return
        label = self.report_customer.currentText() or "All customers"
        fn = exports.export_register_pdf if kind == "pdf" else exports.export_register_xlsx
        self._run_export("Export register", f"Register_{_safe_filename(label)}.{kind}", kind,
                         lambda path: fn(reg, path, self.shop_name(), label))

    def _export_customers(self, kind: str) -> None:
        text = self.rep_cust_filter.text()
        summaries = self.svc.list_customers(text)
        if not summaries:
            self.info("Nothing to export", "No customers match this filter.")
            return
        fn = exports.export_customers_pdf if kind == "pdf" else exports.export_customers_xlsx
        self._run_export("Export customer balances", f"Customer_balances.{kind}", kind,
                         lambda path: fn(summaries, path, self.shop_name(), text.strip()))

    # ------------------------------------------------------------ activity log
    def _build_activity(self) -> QWidget:
        page, layout = self._page()
        act_print = plain_button("Print")
        act_print.clicked.connect(self._print_activity)
        self._header(layout, "Activity", "Every add, edit and delete is recorded here; the app has no way to edit or remove these records",
                     act_print)
        self.audit_search = QLineEdit()
        self.audit_search.setPlaceholderText("Search the activity log…")
        self.audit_search.setClearButtonEnabled(True)
        self.audit_search.textChanged.connect(lambda _t: self._refresh_activity())
        layout.addWidget(self.audit_search)
        self.audit_table = make_table(["When", "Action", "Details"])
        fit_columns(self.audit_table, 2)
        self.audit_table.doubleClicked.connect(lambda _i: self._open_audit_detail())
        layout.addWidget(self.audit_table, 1)
        self.audit_note = QLabel("Double-click a row to see exactly what changed. The latest 500 entries are shown.")
        self.audit_note.setObjectName("muted")
        self.audit_note.setWordWrap(True)
        layout.addWidget(self.audit_note)
        return page

    def _refresh_activity(self) -> None:
        entries = self.svc.audit_entries(500, search=self.audit_search.text())
        self._audit_cache = {e.id: e for e in entries}
        self.audit_table.setRowCount(len(entries))
        danger = theme.current()["danger"]
        for r, e in enumerate(entries):
            set_cell(self.audit_table, r, 0, viewmodel.timestamp_text(e.at), data=e.id)
            set_cell(self.audit_table, r, 1, viewmodel.action_label(e.action), bold=True,
                     color=danger if e.action.endswith("DELETE") else None)
            set_cell(self.audit_table, r, 2, e.summary)

    def _open_audit_detail(self) -> None:
        entry = self._audit_cache.get(selected_id(self.audit_table))
        if entry is not None:
            AuditDetailDialog(entry, parent=self).exec()

    # ------------------------------------------------------------ settings
    def _build_settings(self) -> QWidget:
        outer, outer_layout = self._page()
        self._header(outer_layout, "Settings", "Business details, security, backups and appearance")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        # business + reminder text
        business = Card("Business and reminders")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.shop_edit = QLineEdit()
        self.shop_edit.setPlaceholderText("Shown at the top of every PDF / Excel report and in reminders")
        self.shop_details_edit = QPlainTextEdit()
        self.shop_details_edit.setPlaceholderText("Address, phone, GSTIN – printed under the name on statements")
        self.shop_details_edit.setFixedHeight(64)
        self.country_edit = QLineEdit()
        self.country_edit.setMaximumWidth(90)
        self.country_edit.setPlaceholderText("91")
        self.template_edit = QPlainTextEdit()
        self.template_edit.setFixedHeight(110)
        form.addRow("Business name", self.shop_edit)
        form.addRow("Details", self.shop_details_edit)
        form.addRow("WhatsApp country code", self.country_edit)
        form.addRow("Reminder message", self.template_edit)
        business.body.addLayout(form)
        placeholders = QLabel("You can use: " + "  ".join(insights.TEMPLATE_PLACEHOLDERS))
        placeholders.setObjectName("muted")
        placeholders.setWordWrap(True)
        business.body.addWidget(placeholders)
        business_buttons = QHBoxLayout()
        business_buttons.addStretch(1)
        reset_template = plain_button("Reset message")
        reset_template.clicked.connect(self._reset_template)
        save_business = primary_button("Save")
        save_business.clicked.connect(self._save_business)
        business_buttons.addWidget(reset_template)
        business_buttons.addWidget(save_business)
        business.body.addLayout(business_buttons)
        layout.addWidget(business)

        # appearance
        appearance = Card("Appearance")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Aurora White (glass)", "light")
        self.theme_combo.addItem("Midnight (glass)", "dark")
        self.theme_combo.setMaximumWidth(340)
        self.theme_combo.currentIndexChanged.connect(self._theme_combo_changed)
        appearance.body.addWidget(self.theme_combo)
        layout.addWidget(appearance)

        # receipt printer (thermal slip) - a SEPARATE printer choice from the A4 statements
        receipt_card = Card("Receipt printer (thermal slip, 58 / 80 mm)")
        receipt_form = QFormLayout()
        receipt_form.setVerticalSpacing(10)
        self.receipt_printer_combo = QComboBox()
        self.receipt_printer_combo.setToolTip("Statements keep using the printer chosen in the print dialog. "
                                              "This printer is only for the small entry slips.")
        self.receipt_width_combo = QComboBox()
        self.receipt_width_combo.addItem("80 mm (common)", 80)
        self.receipt_width_combo.addItem("58 mm (small)", 58)
        self.receipt_balance_check = QCheckBox("Show the customer's balance after the entry on the slip")
        receipt_form.addRow("Printer", self.receipt_printer_combo)
        receipt_form.addRow("Paper width", self.receipt_width_combo)
        receipt_card.body.addLayout(receipt_form)
        receipt_card.body.addWidget(self.receipt_balance_check)
        receipt_note = QLabel("Where to print a slip: Ledger page \u2192 select an entry \u2192 'Print receipt', or "
                              "double-click a row in 'Saved in this session' on the New Entry page.")
        receipt_note.setObjectName("muted")
        receipt_note.setWordWrap(True)
        receipt_card.body.addWidget(receipt_note)
        receipt_buttons = QHBoxLayout()
        receipt_test = plain_button("Test slip")
        receipt_test.setToolTip("Print one sample slip with the current settings")
        receipt_test.clicked.connect(self._receipt_test)
        receipt_save = primary_button("Save")
        receipt_save.clicked.connect(self._save_receipt_settings)
        receipt_buttons.addStretch(1)
        receipt_buttons.addWidget(receipt_test)
        receipt_buttons.addWidget(receipt_save)
        receipt_card.body.addLayout(receipt_buttons)
        layout.addWidget(receipt_card)

        # security
        security_card = Card("Security")
        self.pin_status = QLabel("")
        security_card.body.addWidget(self.pin_status)
        pin_note = QLabel("The PIN locks the app window. It does not encrypt the data file, so keep Windows "
                          "user accounts and disk protection in place as well.")
        pin_note.setObjectName("muted")
        pin_note.setWordWrap(True)
        security_card.body.addWidget(pin_note)
        pin_buttons = QHBoxLayout()
        self.pin_set_button = plain_button("Set PIN")
        self.pin_set_button.clicked.connect(self._set_pin)
        self.pin_remove_button = plain_button("Remove PIN", danger=True)
        self.pin_remove_button.clicked.connect(self._remove_pin)
        lock = plain_button("Lock now")
        lock.clicked.connect(self.lock_now)
        pin_buttons.addWidget(self.pin_set_button)
        pin_buttons.addWidget(self.pin_remove_button)
        pin_buttons.addWidget(lock)
        pin_buttons.addStretch(1)
        security_card.body.addLayout(pin_buttons)
        layout.addWidget(security_card)

        # backup & recovery
        data = Card("Backup & Recovery")
        self.data_label = QLabel("")
        self.data_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.data_label.setWordWrap(True)
        data.body.addWidget(self.data_label)
        self.auto_backup_check = QCheckBox("Enable Automatic Backup  (untick to disable; manual backup always works)")
        self.auto_backup_check.toggled.connect(self._auto_backup_toggled)
        data.body.addWidget(self.auto_backup_check)
        freq_row = QHBoxLayout()
        freq_row.addWidget(QLabel("Backup frequency:"))
        self.freq_combo = QComboBox()
        self.freq_combo.addItem("Every 24 hours")
        freq_row.addWidget(self.freq_combo)
        freq_row.addStretch(1)
        data.body.addLayout(freq_row)
        self.backup_info_label = QLabel("")
        self.backup_info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.backup_info_label.setWordWrap(True)
        data.body.addWidget(self.backup_info_label)
        self.auto_backup_label = QLabel("")
        self.auto_backup_label.setObjectName("muted")
        self.auto_backup_label.setWordWrap(True)
        data.body.addWidget(self.auto_backup_label)
        row1 = QHBoxLayout()
        now = primary_button("Backup Now")
        now.clicked.connect(self._backup_now)
        open_backups = plain_button("Open Backup Folder")
        open_backups.clicked.connect(self._open_backup_folder)
        history = plain_button("View Backup History")
        history.clicked.connect(self._show_backup_history)
        choose = plain_button("Change backup folder…")
        choose.clicked.connect(self._choose_backup_folder)
        default_folder = plain_button("Use default folder")
        default_folder.setToolTip("Downloads\\SJB KHATA BACKUP 24")
        default_folder.clicked.connect(self._default_backup_folder)
        for b in (now, open_backups, history, choose, default_folder):
            row1.addWidget(b)
        row1.addStretch(1)
        data.body.addLayout(row1)

        cloud_title = QLabel("Cloud copy (Google Drive + OneDrive - both can be on)")
        cloud_title.setObjectName("h2")
        cloud_title.setWordWrap(True)
        data.body.addWidget(cloud_title)
        self.cloud_info_label = QLabel("")
        self.cloud_info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cloud_info_label.setWordWrap(True)
        data.body.addWidget(self.cloud_info_label)
        cloud_note = QLabel("Backups are copied into folders that Google Drive for desktop and OneDrive keep in sync. "
                            "Without internet the app works as usual and copies the backups later, when a folder is "
                            "available again.")
        cloud_note.setObjectName("muted")
        cloud_note.setWordWrap(True)
        data.body.addWidget(cloud_note)
        cloud_row = QHBoxLayout()
        cloud_od = primary_button("+ OneDrive")
        cloud_od.setToolTip("Switch on cloud copy into your OneDrive folder (found automatically)")
        cloud_od.clicked.connect(lambda: self._add_detected_cloud("OneDrive"))
        cloud_gd = primary_button("+ Google Drive")
        cloud_gd.setToolTip("Switch on cloud copy into your Google Drive folder / drive letter (found automatically)")
        cloud_gd.clicked.connect(lambda: self._add_detected_cloud("Google Drive"))
        cloud_choose = plain_button("Choose folder…")
        cloud_choose.setToolTip("Any other synced folder (Dropbox etc.)")
        cloud_choose.clicked.connect(self._choose_cloud_folder)
        self.cloud_sync_button = plain_button("Copy to cloud now")
        self.cloud_sync_button.clicked.connect(self._sync_cloud_now)
        self.cloud_off_button = plain_button("Turn off cloud copy")
        self.cloud_off_button.clicked.connect(self._cloud_off)
        for b in (cloud_od, cloud_gd, cloud_choose, self.cloud_sync_button, self.cloud_off_button):
            cloud_row.addWidget(b)
        cloud_row.addStretch(1)
        data.body.addLayout(cloud_row)
        row2 = QHBoxLayout()
        restore = plain_button("Restore from Backup…")
        restore.setToolTip("Replace the current data with a backup file. A safety copy is saved first.")
        restore.clicked.connect(self._restore_backup)
        create = plain_button("Export database…")
        create.setToolTip("Save one .db file with ALL your data (customers, entries, settings)")
        create.clicked.connect(self._create_backup)
        open_folder = plain_button("Open data folder")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.svc.db_path.parent))))
        for b in (restore, create, open_folder):
            row2.addWidget(b)
        row2.addStretch(1)
        data.body.addLayout(row2)
        layout.addWidget(data)

        # updates - the only part of the app that ever uses the internet, and only for a version number
        layout.addWidget(self._update_card())

        layout.addStretch(1)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        return outer

    # -- voice assistant page
    def _build_voice(self) -> QWidget:
        outer, outer_layout = self._page()
        test = primary_button("Test voice")
        test.clicked.connect(self._voice_test)
        defaults = plain_button("Restore defaults")
        defaults.clicked.connect(self._voice_restore_defaults)
        self._header(outer_layout, "Voice assistant",
                     "Important activity spoken out loud. Everything runs inside this PC - no internet, "
                     "no cloud, nothing sent anywhere.", test, defaults)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._voice_status_card())
        left.addWidget(self._voice_controls_card())
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._voice_history_card(), 1)
        columns.addLayout(left, 2)
        columns.addLayout(right, 3)
        layout.addLayout(columns)
        layout.addWidget(self._voice_events_card())

        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        return outer

    def _voice_status_card(self) -> QWidget:
        card = Card("Assistant")
        self.voice_status = QLabel("")
        self.voice_status.setObjectName("h2")
        self.voice_status.setWordWrap(True)
        card.body.addWidget(self.voice_status)
        self.voice_detail = QLabel("")
        self.voice_detail.setObjectName("muted")
        self.voice_detail.setWordWrap(True)
        card.body.addWidget(self.voice_detail)

        self.voice_enabled = QCheckBox("Speak announcements")
        self.voice_enabled.toggled.connect(self._save_voice_settings)
        card.body.addWidget(self.voice_enabled)

        top = QHBoxLayout()
        self.voice_mute = plain_button("Mute")
        self.voice_mute.setToolTip("Stop speaking until you press Unmute (does not change any other setting)")
        self.voice_mute.clicked.connect(self._voice_mute_toggled)
        top.addWidget(self.voice_mute)
        top.addStretch(1)
        card.body.addLayout(top)

        self.voice_quiet = QCheckBox("Quiet mode (warnings only)")
        self.voice_quiet.toggled.connect(self._save_voice_settings)
        self.voice_public = QCheckBox("Public mode (never say a customer's name)")
        self.voice_public.setToolTip("For counters with customers standing close by: amounts stay, names do not")
        self.voice_public.toggled.connect(self._save_voice_settings)
        card.body.addWidget(self.voice_quiet)
        card.body.addWidget(self.voice_public)
        return card

    def _voice_controls_card(self) -> QWidget:
        card = Card("Voice")
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.voice_language = QComboBox()
        for code in ("hinglish", "hi", "en"):
            self.voice_language.addItem(LANGUAGE_LABELS.get(code, code), code)
        self.voice_language.setToolTip("Words the assistant speaks. The voice model that answers in that language "
                                       "has to be installed - the card above says which one is in use")
        self.voice_language.setMaximumWidth(220)
        self.voice_language.currentIndexChanged.connect(self._save_voice_settings)
        self.voice_gender = QComboBox()
        self.voice_gender.addItem("Female voice", "female")
        self.voice_gender.addItem("Male voice", "male")
        self.voice_gender.setToolTip("Switches between the male and female recordings inside the installed voice. "
                                     "The line under the heading names the one actually in use")
        self.voice_gender.setMaximumWidth(220)
        self.voice_gender.currentIndexChanged.connect(self._save_voice_settings)
        self.voice_volume = QSpinBox()
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setSuffix(" %")
        self.voice_volume.setMaximumWidth(110)
        self.voice_volume.valueChanged.connect(self._save_voice_settings)
        self.voice_speed = QSpinBox()
        self.voice_speed.setRange(50, 200)
        self.voice_speed.setSuffix(" %")
        self.voice_speed.setToolTip("100 % is the voice's own pace")
        self.voice_speed.setMaximumWidth(110)
        self.voice_speed.valueChanged.connect(self._save_voice_settings)
        self.voice_pitch = QSpinBox()
        self.voice_pitch.setRange(-12, 12)
        self.voice_pitch.setEnabled(False)
        self.voice_pitch.setToolTip("The bundled offline voice engine has no pitch control, so this is disabled "
                                    "instead of pretending to change the voice")
        self.voice_pitch.setMaximumWidth(110)
        form.addRow("Language", self.voice_language)
        form.addRow("Voice", self.voice_gender)
        form.addRow("Volume", self.voice_volume)
        form.addRow("Speaking speed", self.voice_speed)
        form.addRow("Pitch", self.voice_pitch)
        card.body.addLayout(form)

        clear = plain_button("Clear history")
        clear.clicked.connect(self._voice_clear_history)
        row = QHBoxLayout()
        row.addWidget(clear)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _voice_events_card(self) -> QWidget:
        card = Card("What to announce")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.voice_event_boxes: dict[str, QCheckBox] = {}
        row = 0
        for category in dict.fromkeys(event.category for event in EVENT_CATALOGUE):
            heading = QLabel(category)
            heading.setObjectName("muted")
            grid.addWidget(heading, row, 0, 1, 2)
            row += 1
            column = 0
            for event in (e for e in EVENT_CATALOGUE if e.category == category):
                box = QCheckBox(event.label)
                box.toggled.connect(lambda on, name=event.name: self._voice_event_toggled(name, on))
                self.voice_event_boxes[event.name] = box
                grid.addWidget(box, row, column)
                column += 1
                if column == 2:
                    column = 0
                    row += 1
            if column:
                row += 1
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    def _voice_history_card(self) -> QWidget:
        card = Card("Announcement history")
        self.voice_history_table = make_table(["When", "Event", "Announcement", "Result"])
        self.voice_history_table.setMinimumHeight(160)
        fit_columns(self.voice_history_table, 2)
        card.body.addWidget(self.voice_history_table)
        return card

    def _voice_settings_from_ui(self) -> VoiceSettings:
        return VoiceSettings(
            enabled=self.voice_enabled.isChecked(),
            language=self.voice_language.currentData() or "hinglish",
            gender=self.voice_gender.currentData() or "female",
            volume=self.voice_volume.value(),
            speed=self.voice_speed.value(),
            pitch=self.voice_pitch.value(),
            quiet=self.voice_quiet.isChecked(),
            public=self.voice_public.isChecked(),
            engine_path=VoiceSettings.load(self.svc).engine_path,   # set on disk by hand, not from this panel
            events={name: box.isChecked() for name, box in self.voice_event_boxes.items()},
        )

    def _save_voice_settings(self, *_args) -> None:
        manager = voice.current()
        if manager is None:
            return
        self._voice_settings_from_ui().save(self.svc)
        manager.reload()
        self._refresh_voice_settings()

    def _voice_event_toggled(self, name: str, on: bool) -> None:
        manager = voice.current()
        current = VoiceSettings.load(self.svc).with_event(name, on)
        current.save(self.svc)
        if manager is not None:
            manager.reload()

    def _voice_mute_toggled(self) -> None:
        manager = voice.current()
        if manager is None:
            return
        manager.set_muted(not manager.muted)
        self._refresh_voice_settings()

    def _voice_test(self) -> None:
        manager = voice.current()
        if manager is None:
            return
        manager.reload()   # a setting changed a moment ago must apply to the test as well
        if manager.status():
            self.statusBar().showMessage(manager.status(), 8000)
        if manager.test():
            self.statusBar().showMessage("Test sentence queued…", 4000)

    def _voice_restore_defaults(self) -> None:
        if not self.confirm("Voice settings", "Restore every voice setting to its default?", default_no=True):
            return
        VoiceSettings().resets(self.svc)
        manager = voice.current()
        if manager is not None:
            manager.reload()
        self._refresh_voice_settings()

    def _voice_clear_history(self) -> None:
        voice_history.clear()
        self._refresh_voice_settings()

    def _refresh_voice_settings(self) -> None:
        manager = voice.current()
        settings = manager.settings if manager is not None else VoiceSettings.load(self.svc)
        for widget, value in ((self.voice_enabled, settings.enabled), (self.voice_quiet, settings.quiet),
                              (self.voice_public, settings.public)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        for combo, value in ((self.voice_language, settings.language), (self.voice_gender, settings.gender)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        for spin, value in ((self.voice_volume, settings.volume), (self.voice_speed, settings.speed),
                            (self.voice_pitch, settings.pitch)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        for name, box in self.voice_event_boxes.items():
            box.blockSignals(True)
            box.setChecked(settings.speaks(name))
            box.blockSignals(False)

        muted = manager is not None and manager.muted
        self.voice_mute.setText("Unmute" if muted else "Mute")
        if manager is None:
            self.voice_status.setText("The voice assistant is not running.")
            self.voice_detail.setText("")
        else:
            model = manager.voice_for()
            ready = manager.status()
            if ready:
                self.voice_status.setText("Silent right now: " + ready)
            elif model is not None:
                self.voice_status.setText(f"Ready: {model.label} ({LANGUAGE_LABELS.get(model.language, model.language)})"
                                          "  –  speaks without internet")
            else:
                self.voice_status.setText("Ready.")
            self.voice_detail.setText(manager.information())
        entries = voice_history.recent(20)
        table = self.voice_history_table
        table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            set_cell(table, i, 0, str(entry.get("at", "")))
            set_cell(table, i, 1, str(entry.get("event", "")))
            set_cell(table, i, 2, str(entry.get("text", "")))
            spoken = bool(entry.get("spoken"))
            reason = str(entry.get("reason", ""))
            set_cell(table, i, 3, "Spoken" if spoken else f"Not spoken – {reason}" if reason else "Not spoken",
                     bold=not spoken)

    # ------------------------------------------------------------ updates
    def _update_card(self) -> Card:
        card = Card("Updates")
        self.update_status = QLabel("")
        self.update_status.setWordWrap(True)
        card.body.addWidget(self.update_status)
        self.update_notes = QLabel("")
        self.update_notes.setObjectName("muted")
        self.update_notes.setWordWrap(True)
        self.update_notes.hide()
        card.body.addWidget(self.update_notes)
        self.update_auto = QCheckBox("Check for updates automatically when this PC is online")
        self.update_auto.setToolTip("Only the latest version number is fetched from GitHub. No shop name, no "
                                    "customers, no entries and no data file ever leave this PC.")
        self.update_auto.toggled.connect(self._update_auto_toggled)
        card.body.addWidget(self.update_auto)
        row = QHBoxLayout()
        self.update_check_button = plain_button("Check now")
        self.update_check_button.clicked.connect(lambda: self._check_updates(manual=True))
        self.update_install_button = primary_button("Download & install")
        self.update_install_button.clicked.connect(self._start_update)
        self.update_install_button.hide()
        row.addWidget(self.update_check_button)
        row.addWidget(self.update_install_button)
        row.addStretch(1)
        card.body.addLayout(row)
        note = QLabel("Works fully offline, exactly as before. Before an update is installed a full safety backup "
                      "of the data file is written; if the new version cannot read the data, the app offers to "
                      "restore that backup.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _update_auto_enabled(self) -> bool:
        return self.svc.get_setting("update.auto", "1") != "0"

    def _update_auto_toggled(self, on: bool) -> None:
        self.svc.set_setting("update.auto", "1" if on else "0")
        if on:
            self._maybe_check_updates()

    def _maybe_check_updates(self) -> None:
        """Once a day at most, and never when the user switched automatic checks off."""
        if not self._update_auto_enabled():
            self._refresh_update_labels()
            return
        last = self.svc.get_setting("update.last_check", "")
        try:
            when = datetime.fromisoformat(last)
        except ValueError:
            when = None
        if when is not None and datetime.now() - when < timedelta(hours=24):
            self._refresh_update_labels()
            return
        self._check_updates(manual=False)

    def _check_updates(self, manual: bool) -> None:
        if self._update_thread is not None:
            return
        self.update_check_button.setEnabled(False)
        if manual:
            self.update_status.setText("Checking for updates…")
        thread = UpdateCheckThread(self)
        thread.found.connect(lambda release: self._update_found(release, manual))
        thread.failed.connect(lambda message: self._update_check_failed(message, manual))
        thread.finished.connect(thread.deleteLater)
        self._update_thread = thread
        thread.start()

    def _update_found(self, release, manual: bool) -> None:
        self._update_thread = None
        self.update_check_button.setEnabled(True)
        self.svc.set_setting("update.last_check", datetime.now().isoformat(timespec="seconds"))
        if updates.is_newer(release.version):
            self._available_release = release
            self.update_status.setText(f"Update available: v{release.version}   (you have v{__version__})")
            self.update_notes.setText(release.notes[:2000] or "See the release page for details.")
            self.update_notes.show()
            self.update_install_button.setText(f"Download & install v{release.version}")
            self.update_install_button.show()
            self.statusBar().showMessage(f"Update v{release.version} available - Settings > Updates", 15000)
        else:
            self._available_release = None
            self.update_status.setText(f"You are on the latest version (v{__version__})."
                                       if manual else f"Up to date (v{__version__}).")
            self.update_notes.hide()
            self.update_install_button.hide()

    def _update_check_failed(self, message: str, manual: bool) -> None:
        self._update_thread = None
        self.update_check_button.setEnabled(True)
        if manual:
            self.update_status.setText(message)
        else:
            self._refresh_update_labels()   # a failed background check stays quiet, exactly like an offline PC

    def _refresh_update_labels(self) -> None:
        if not hasattr(self, "update_auto"):
            return
        self.update_auto.blockSignals(True)
        self.update_auto.setChecked(self._update_auto_enabled())
        self.update_auto.blockSignals(False)
        if self._available_release is None:
            self.update_status.setText(f"Current version: v{__version__}. Online checks run at most once a day.")

    def _start_update(self) -> None:
        release = self._available_release
        if release is None or self._download_thread is not None:
            return
        if not self.confirm(
                "Install update",
                f"VX7 KHATA PRO will update from v{__version__} to v{release.version}.\n\n"
                "First a full safety backup of your data file is written. Then the app closes, the installer "
                "runs quietly and the app reopens with all your data, settings and the licence untouched.\n\n"
                "Continue?"):
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = paths.safety_backup_dir() / f"before-update-{release.version}-{stamp}.db"
        try:
            self.svc.create_backup(safety)
            backup.validate_backup(safety)
        except (backup.BackupError, OSError) as exc:
            self.warn("Update stopped",
                      f"The safety backup could not be made, so nothing was changed.\n\n{exc}")
            return
        usb_copy = updates.copy_backup_to_removable(safety)
        if usb_copy is not None:
            self.statusBar().showMessage(f"Safety backup also copied to {usb_copy.parent}", 15000)
        try:
            self._pending_snapshot = updates.data_snapshot(self.svc)
        except (KhataError, sqlite3.Error):
            self._pending_snapshot = {}
        self._pending_backup_path = str(safety)

        dialog = QProgressDialog("Downloading the update…", "Cancel", 0, 100, self)
        dialog.setWindowTitle("VX7 KHATA PRO update")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        thread = UpdateDownloadThread(release, Path(paths.data_dir()) / "updates", self)
        thread.progress.connect(lambda got, total: dialog.setValue(int(got * 100 / total) if total else 0))
        thread.saved.connect(lambda path: self._update_downloaded(path, dialog))
        thread.failed.connect(lambda message: self._update_download_failed(message, dialog, thread))
        dialog.canceled.connect(thread.cancel)
        thread.finished.connect(thread.deleteLater)
        self._update_dialog = dialog
        self._download_thread = thread
        thread.start()

    def _update_downloaded(self, path: str, dialog: QProgressDialog) -> None:
        dialog.close()
        dialog.deleteLater()
        self._update_dialog = None
        self._download_thread = None
        installer = Path(path)
        release = self._available_release
        if not installer.is_file() or release is None:
            self.warn("Update failed", "The installer file disappeared before it could be started.")
            return
        # The marker is written only now, when the installer is about to run; a cancelled download must never
        # leave a pending update behind that the next start would treat as a finished one.
        self.svc.set_setting("update.pending_version", release.version)
        self.svc.set_setting("update.pending_from", __version__)
        self.svc.set_setting("update.pending_backup", getattr(self, "_pending_backup_path", ""))
        self.svc.set_setting("update.pending_counts", json.dumps(getattr(self, "_pending_snapshot", {})))
        try:
            updates.launch_installer(installer)
        except OSError as exc:
            self.warn("Update failed", f"The installer could not be started:\n\n{exc}")
            return
        QMessageBox.information(self, "Installing",
                                "The update is installing. This window will close; open the app again in a "
                                "moment. Your data was backed up before the update.")
        QApplication.quit()

    def _update_download_failed(self, message: str, dialog: QProgressDialog, thread) -> None:
        dialog.close()
        dialog.deleteLater()
        self._update_dialog = None
        self._download_thread = None
        if "cancelled" in message.lower():
            self.update_status.setText("Download cancelled. Nothing was changed.")
            return
        self.warn("Update failed", message)

    def _finish_pending_update(self) -> None:
        """First start after an update: prove the data came through, else offer the pre-update backup back."""
        pending = self.svc.get_setting("update.pending_version", "")
        if not pending:
            return
        pending_from = self.svc.get_setting("update.pending_from", "")
        if updates.version_tuple(pending) > updates.version_tuple(__version__):
            if pending_from == __version__:
                # The installer never ran (cancelled at the Windows prompt, or it failed): forget the marker.
                self._clear_pending_update()
            return
        if updates.version_tuple(pending) < updates.version_tuple(__version__) and pending_from != __version__:
            self._clear_pending_update()      # an old marker from a different version chain - not ours to judge
            return
        backup_path = self.svc.get_setting("update.pending_backup", "")
        try:
            snapshot = json.loads(self.svc.get_setting("update.pending_counts", "") or "{}")
        except ValueError:
            snapshot = {}
        ok, detail = updates.verify_data(self.svc, snapshot)
        self._clear_pending_update()
        if ok:
            self.statusBar().showMessage(f"Updated to v{__version__} - your data checked out fine.", 12000)
            return
        if not backup_path or not Path(backup_path).is_file():
            self.warn("Data check after the update", f"{detail}\n\nThe safety backup file is missing.")
            return
        if self.confirm("Data check after the update",
                        f"{detail}\n\nRestore the safety backup that was made just before the update?"):
            try:
                self.svc.restore_backup(backup_path)
                self.refresh_all()
                QMessageBox.information(self, "Restored", "Your data was restored from the safety backup.")
            except (backup.BackupError, OSError) as exc:
                self.warn("Restore failed", str(exc))

    def _clear_pending_update(self) -> None:
        for key in ("update.pending_version", "update.pending_from", "update.pending_backup",
                    "update.pending_counts"):
            self.svc.set_setting(key, "")

    def _refresh_settings(self) -> None:
        self.shop_edit.setText(self.shop_name())
        self.shop_details_edit.setPlainText(self.shop_details())
        self.country_edit.setText(self.svc.get_setting(insights.SETTING_COUNTRY_CODE, insights.DEFAULT_COUNTRY_CODE))
        self.template_edit.setPlainText(insights.get_template(self.svc))

        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(theme.current_name())))
        self.theme_combo.blockSignals(False)

        has_pin = security.has_pin(self.svc)
        self.pin_status.setText("PIN lock is ON" if has_pin else "PIN lock is OFF")
        self.pin_set_button.setText("Change PIN" if has_pin else "Set PIN")
        self.pin_remove_button.setEnabled(has_pin)
        self._refresh_receipt_settings()
        self._refresh_voice_settings()
        self._refresh_update_labels()

        d = self.svc.dashboard()
        self.data_label.setText(f"Data file: {self.svc.db_path}\n{d.total_customers} customers, "
                                f"{d.total_transactions} entries.")
        mgr = self.svc.backups
        st = mgr.status()
        self.auto_backup_check.blockSignals(True)
        self.auto_backup_check.setChecked(st.enabled)
        self.auto_backup_check.blockSignals(False)
        fmt = "%d-%m-%Y %H:%M:%S"
        last = st.last_success.strftime(fmt) if st.last_success else "No backup yet"
        nxt = st.next_due.strftime(fmt) if st.next_due else "Automatic backup is off"
        self.backup_info_label.setText(
            f"Backup status: {st.status}\nLast successful backup: {last}\nNext scheduled backup: {nxt}\n"
            f"Current backup folder: {st.folder}   ({st.backups_in_folder} backup file"
            f"{'s' if st.backups_in_folder != 1 else ''} stored)")
        cs = mgr.cloud_status()
        folders = mgr.cloud_folders()
        if not folders:
            self.cloud_info_label.setText("Cloud copy is OFF. Use + OneDrive / + Google Drive (or choose any synced "
                                          "folder) to switch it on.")
        else:
            last_cloud = cs.last_success.strftime(fmt) if cs.last_success else "Not yet"
            lines = []
            for f in folders:
                where = "ready" if f.is_dir() else "NOT available now (offline or the app is not running); will retry"
                lines.append(f"Cloud folder: {f}   ({where})")
            lines.append(f"Last cloud copy: {last_cloud}")
            lines.append(f"Waiting to be copied: {cs.pending} backup{'s' if cs.pending != 1 else ''}")
            if cs.error:
                lines.append(f"Last problem: {cs.error}")
            self.cloud_info_label.setText("\n".join(lines))
        self.cloud_sync_button.setEnabled(bool(folders))
        self.cloud_off_button.setEnabled(bool(folders))
        note = ("Old backups are never deleted by the app. While the app is closed, Windows Task Scheduler makes the "
                "24-hour backup; if that is unavailable, an overdue backup is made the next time the app starts.")
        self.auto_backup_label.setText((f"Last error: {st.last_error}\n\n" if st.last_error and st.status == "Failed" else "") + note)

    def _save_business(self) -> None:
        country = "".join(ch for ch in self.country_edit.text() if ch.isdigit())
        if not country:
            self.warn("Country code", "Enter the WhatsApp country code as digits, for example 91.")
            return
        try:
            insights.set_template(self.svc, self.template_edit.toPlainText())
        except KhataError as exc:
            self.warn("Reminder message", str(exc))
            return
        lines = [line.strip() for line in self.shop_details_edit.toPlainText().splitlines() if line.strip()]
        self.svc.set_setting("shop_name", " ".join(self.shop_edit.text().split()))
        self.svc.set_setting("shop_details", "\n".join(lines[:4]))
        self.svc.set_setting(insights.SETTING_COUNTRY_CODE, country)
        voice.announce("data_saved")
        self.statusBar().showMessage("Settings saved", 4000)
        self._refresh_settings()

    def _reset_template(self) -> None:
        insights.set_template(self.svc, "")
        self.template_edit.setPlainText(insights.get_template(self.svc))

    # -- receipt printer (thermal slip)
    def _refresh_receipt_settings(self) -> None:
        from PySide6.QtPrintSupport import QPrinterInfo
        setup = receipt.load(self.svc)
        combo = self.receipt_printer_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Ask every time (print dialog)", "")
        default_name = QPrinterInfo.defaultPrinter().printerName()
        for info in QPrinterInfo.availablePrinters():
            name = info.printerName()
            label = name + ("  (Windows default)" if name == default_name else "")
            combo.addItem(label, name)
        index = combo.findData(setup.printer)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        width_index = self.receipt_width_combo.findData(setup.width_mm)
        self.receipt_width_combo.setCurrentIndex(width_index if width_index >= 0 else 0)
        self.receipt_balance_check.setChecked(setup.show_balance)

    def _receipt_setup_from_ui(self) -> receipt.ReceiptSetup:
        return receipt.ReceiptSetup(
            printer=self.receipt_printer_combo.currentData() or "",
            width_mm=self.receipt_width_combo.currentData() or receipt.DEFAULT_WIDTH_MM,
            show_balance=self.receipt_balance_check.isChecked(),
        )

    def _save_receipt_settings(self) -> None:
        receipt.save(self.svc, self._receipt_setup_from_ui())
        self.statusBar().showMessage("Receipt settings saved", 4000)

    def _receipt_test(self) -> None:
        from types import SimpleNamespace
        receipt.save(self.svc, self._receipt_setup_from_ui())   # the test uses what is on screen right now
        setup = receipt.load(self.svc)
        customer = SimpleNamespace(name="Sample Customer", mobile="98765 43210")
        txn = SimpleNamespace(id=1, txn_date=self.svc.today().isoformat(), item="Sample item (Atta 5kg)",
                              quantity="2", rate_paise=14500, amount_paise=29000, txn_type="UDHAAR",
                              notes="This is a test slip - no real entry.", due_date=None)
        html_text = receipt.build_receipt_html(self.shop_name() or "YOUR SHOP NAME", self.shop_details(),
                                               customer, txn, width_mm=setup.width_mm, balance_paise=29000,
                                               print_time=datetime.now().strftime("%d-%m-%Y %H:%M"))
        self._send_receipt(html_text, setup)

    # -- theme
    def _update_theme_button(self) -> None:
        self.theme_button.setText("Switch to Aurora White" if theme.current_name() == "dark" else "Switch to dark mode")

    def _apply_theme(self, name: str) -> None:
        theme.set_current(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet(name))
        refresh_shadows(self)
        refresh_glyphs(self)
        self._update_theme_button()
        self.chart.update()
        self.refresh_all()

    def _toggle_theme(self) -> None:
        name = "light" if theme.current_name() == "dark" else "dark"
        self.svc.set_setting("theme", name)
        self._apply_theme(name)

    def _theme_combo_changed(self, _index: int) -> None:
        name = self.theme_combo.currentData() or "light"
        self.svc.set_setting("theme", name)
        self._apply_theme(name)

    # -- PIN / lock
    def _set_pin(self) -> None:
        if PinSetupDialog(self.svc, parent=self).exec():
            self._refresh_settings()

    def _remove_pin(self) -> None:
        pin, ok = QInputDialog.getText(self, "Remove PIN", "Enter the current PIN to remove the lock:",
                                       QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            security.clear_pin(self.svc, pin)
        except KhataError as exc:
            self.warn("Remove PIN", str(exc))
            return
        self._refresh_settings()
        self.statusBar().showMessage("PIN removed", 4000)

    def lock_now(self) -> None:
        if not security.has_pin(self.svc):
            self.info("No PIN set", "Set a PIN under Settings > Security first.")
            return
        if self._locked:
            return
        self._locked = True
        root = self.centralWidget()
        root.setVisible(False)  # hide the data while the PIN screen is up
        voice.announce("locked")
        try:
            unlocked = LockDialog(self.svc, self.guard, self).exec()
        finally:
            root.setVisible(True)
            self._locked = False
        if unlocked:
            voice.announce("unlocked")
        else:
            self.close()

    # -- backups
    def _auto_backup_toggled(self, on: bool) -> None:
        from .. import autobackup
        self.svc.backups.set_enabled(on)
        ok, msg = autobackup.install_task() if on else autobackup.remove_task()
        if on and not ok and autobackup._is_windows():
            self.warn("Background schedule not set up", f"{msg}\n\nAutomatic backup will still run whenever the app "
                      "is open, and an overdue backup is made when the app next starts.")
        if on:
            self._auto_backup_tick()
        self._refresh_settings()

    def _choose_backup_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the backup folder", str(self.svc.backups.folder().parent))
        if folder:
            self.svc.backups.set_folder(folder)
            self._refresh_settings()

    def _add_detected_cloud(self, name: str) -> None:
        from .. import autobackup
        path = autobackup.detect_cloud_folders().get(name)
        if path is None:
            self.warn(name, f"{name} was not found on this PC.\n\nInstall / sign in to the {name} desktop app first, "
                            "or use 'Choose folder…' to pick its folder yourself.")
            return
        if not self.svc.backups.add_cloud_folder(path):
            self.info(name, f"{name} cloud copy is already ON:\n{path}")
            return
        copied = self.svc.backups.sync_cloud()
        self._refresh_settings()
        cs = self.svc.backups.cloud_status(path)
        if cs.error:
            self.warn("Cloud copy", f"The folder is saved, but copying failed for now:\n{cs.error}")
        else:
            self.info("Cloud copy is ON", f"{name}: {copied} existing backup{'s' if copied != 1 else ''} copied to:\n"
                                          f"{path}\n\nFrom now on every backup is copied there automatically.")

    def _choose_cloud_folder(self) -> None:
        start = self.svc.backups.cloud_folder() or Path(_documents_dir())
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder synced by Google Drive / OneDrive / Dropbox",
                                                  str(start))
        if not folder:
            return
        self.svc.backups.add_cloud_folder(folder)
        copied = self.svc.backups.sync_cloud()
        self._refresh_settings()
        cs = self.svc.backups.cloud_status(Path(folder))
        if cs.error:
            self.warn("Cloud copy", f"The folder is saved, but copying failed for now:\n{cs.error}")
        else:
            self.info("Cloud copy is ON", f"{copied} existing backup{'s' if copied != 1 else ''} copied to:\n{folder}\n\n"
                                          "From now on every backup is copied there automatically.")

    def _sync_cloud_now(self) -> None:
        copied = self.svc.backups.sync_cloud()
        self._refresh_settings()
        cs = self.svc.backups.cloud_status()
        if cs.error:
            self.warn("Cloud copy", f"Could not copy to the cloud folder:\n{cs.error}")
        else:
            self.info("Cloud copy", f"{copied} backup{'s' if copied != 1 else ''} copied. Everything is up to date.")

    def _cloud_off(self) -> None:
        if self.confirm("Turn off cloud copy", "Stop copying backups to all cloud folders?\n"
                        "Files already there are kept."):
            self.svc.backups.set_cloud_folder(None)
            self._refresh_settings()

    def _default_backup_folder(self) -> None:
        self.svc.backups.set_folder(None)
        self._refresh_settings()

    def _open_backup_folder(self) -> None:
        folder = self.svc.backups.folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.warn("Backup folder", f"The folder could not be created:\n{folder}\n\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _backup_now(self) -> None:
        try:
            path = self.svc.auto_backup_if_due(force=True)
        except (backup.BackupError, OSError) as exc:
            voice.announce("backup_failed")
            self._refresh_settings()
            self.warn("Backup failed", str(exc))
            return
        self._refresh_settings()
        voice.announce("backup_done")
        self.info("Backup complete", f"Saved and verified:\n{path}")

    def _show_backup_history(self) -> None:
        from PySide6.QtWidgets import QDialog

        dlg = QDialog(self)
        dlg.setWindowTitle("Backup history")
        dlg.resize(900, 480)
        lay = QVBoxLayout(dlg)
        table = make_table(["When", "Result", "Kind", "Details", "File"])
        entries = self.svc.backups.history()
        table.setRowCount(len(entries))
        for i, h in enumerate(entries):
            when = h.get("at", "")
            try:
                when = datetime.strptime(when, "%Y-%m-%dT%H:%M:%S").strftime("%d-%m-%Y %H:%M:%S")
            except ValueError:
                pass
            set_cell(table, i, 0, when)
            set_cell(table, i, 1, "Success" if h.get("ok") else "Failed", bold=True)
            set_cell(table, i, 2, str(h.get("kind", "")))
            set_cell(table, i, 3, str(h.get("message", "")))
            set_cell(table, i, 4, str(h.get("file", "")))
        fit_columns(table, 3)
        lay.addWidget(table)
        if not entries:
            lay.addWidget(QLabel("No backups have been attempted yet."))
        close = plain_button("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _create_backup(self) -> None:
        default = f"VX7_KHATA_database_{datetime.now():%Y%m%d_%H%M%S}.db"
        path = self._export_path("Export database", default, "VX7 KHATA database (*.db)")
        if not path:
            return
        try:
            self.svc.create_backup(path)
            info = backup.validate_backup(path)
        except (backup.BackupError, OSError) as exc:
            voice.announce("backup_failed")
            self.warn("Backup failed", str(exc))
            return
        voice.announce("backup_done")
        self.info("Database exported", f"Saved and verified:\n{path}\n\n{info.customers} customers, "
                  f"{info.transactions} entries.\n\nUse Settings > Import database to load it on this or another PC.")

    def _restore_backup(self) -> None:
        start = str(self.svc.backups.folder()) if self.svc.backups.folder().is_dir() else _documents_dir()
        path, _ = QFileDialog.getOpenFileName(self, "Choose the backup file to restore", start,
                                              "VX7 KHATA database (*.db);;All files (*)")
        if not path:
            return
        try:
            info = backup.validate_backup(path)
        except backup.BackupError as exc:
            self.warn("This backup file cannot be used", str(exc))
            return
        current = self.svc.dashboard()
        text = (f"Restore this backup?\n\n{path}\n\nIt contains {info.customers} customers and "
                f"{info.transactions} entries.\nYour current data ({current.total_customers} customers, "
                f"{current.total_transactions} entries) will be REPLACED, including settings such as the PIN. "
                "If this backup is older than your current data, every customer, entry and balance added or changed "
                "after it was made will be gone from the app.\n\n"
                "A safety copy of the current data is saved first.")
        if not self.confirm("Restore from Backup", text):
            return
        try:
            safety = self.svc.restore_backup(path, safety_dir=self.svc.db_path.parent / "safety_backups")
        except (backup.BackupError, KhataError, OSError) as exc:
            voice.announce("restore_failed")
            self.warn("Restore failed", f"{exc}\n\nYour current data was not changed.")
            return
        self._apply_theme(self.svc.get_setting("theme", "light"))
        assistant = voice.current()
        if assistant is not None:
            assistant.clear()      # sentences queued about the data that has just been replaced
            assistant.reload()     # the backup file carried its own voice preferences
        voice.announce("restore_done")
        self.refresh_all()
        self.info("Restore complete", f"Backup restored.\n\nSafety copy of your previous data:\n{safety}")

    # ------------------------------------------------------------ lifecycle
    def _minute_tick(self) -> None:
        """Runs once a minute while the app is open: waiting Daily Entry rows, then the 24-hour automatic backup."""
        self._auto_post_pending()
        self._auto_backup_tick()

    def _auto_backup_tick(self) -> None:
        """Make the automatic backup as soon as the newest one is 24 hours old - also when the app stays open for days."""
        if self._saving or not self.svc.backups.enabled():
            return
        if self._backup_retry_at is not None and datetime.now() < self._backup_retry_at:
            return
        try:
            made = self.svc.auto_backup_if_due()
        except (backup.BackupError, OSError) as exc:
            self._backup_retry_at = datetime.now() + timedelta(hours=1)
            voice.announce("backup_failed")
            self.statusBar().showMessage(f"Automatic backup failed: {exc}", 12000)
            return
        self._backup_retry_at = None
        if made is not None:
            voice.announce("backup_done")
            self.statusBar().showMessage(f"Automatic backup saved: {made.name}", 8000)
            if self.current_key == "settings":
                self._refresh_settings()

    def _refresh_date_limits(self) -> None:
        """Every date box in the app (including the Daily Entry rows): move on after midnight, never go stale."""
        for field in self.findChildren(DateField):
            field.refresh_limits()

    def _auto_post_pending(self) -> None:
        """Save every Daily Entry row whose 24 hours are over (also covers the time the app was closed)."""
        self._refresh_date_limits()
        if self._saving:
            return
        try:
            posted = self.svc.post_due_pending()
        except (KhataError, sqlite3.Error) as exc:
            self.statusBar().showMessage(f"Could not save waiting Daily Entry rows: {exc}", 8000)
            return
        if posted:
            voice.announce("daily_posted", count=len(posted))
            self.refresh_all()
            self.statusBar().showMessage(f"{len(posted)} Daily Entry row(s) saved into the ledgers after 24 hours", 10000)
        elif self.current_key == "daily":
            self.daily_page.refresh_pending()  # keeps the 'Saves in' countdown up to date

    def run_startup_tasks(self) -> None:
        """Save Daily Entry rows that became due while the app was closed, then the daily automatic backup.
        Called once after the window is shown."""
        self._finish_pending_update()
        self._maybe_check_updates()
        self._auto_post_pending()
        if not self.svc.backups.enabled():
            return
        from .. import autobackup
        if autobackup._is_windows() and not autobackup.task_exists():
            autobackup.install_task()   # background schedule for when the app is closed (best effort)
        try:
            self.svc.auto_backup_if_due()   # overdue backup (PC was off / app closed) is made right away
        except (backup.BackupError, OSError) as exc:
            self.warn("Automatic backup failed", f"{exc}\n\nChoose another folder under Settings > Backup & Recovery.")

    def closeEvent(self, event) -> None:
        daily = getattr(self, "daily_page", None)
        if daily is not None and daily.has_unsaved():
            box = QMessageBox(QMessageBox.Icon.Question, "Unsaved Daily Entry items",
                              "The Daily Entry page still has typed entries that were NOT added.\n\n"
                              "Add them to the waiting list now?",
                              QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                              | QMessageBox.StandardButton.Cancel, self)
            box.setDefaultButton(QMessageBox.StandardButton.Save)
            choice = box.exec()
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                daily.save_all()
                if daily.has_unsaved():        # a validation problem kept entries on the page
                    self.show_page("daily")
                    event.ignore()
                    return
            else:
                daily.clear_draft()            # thrown away on purpose: do not offer it again on the next start
        try:
            if self.svc.backups.enabled():
                self.svc.auto_backup_if_due()
        except Exception:  # noqa: BLE001 - a failed backup must never stop the app from closing
            pass
        voice.announce("app_closing")
        super().closeEvent(event)

    def _offer_draft_restore(self) -> None:
        """After start-up: offer to bring back Daily Entry items typed before the app was last closed."""
        daily = getattr(self, "daily_page", None)
        if daily is None or not daily.has_draft() or daily.has_unsaved():
            return
        if self.confirm("Unsaved entries found",
                        "The Daily Entry page still had typed entries when the app was last closed.\n\n"
                        "Restore them?", default_no=False):
            count = daily.load_draft()
            if count:
                self.show_page("daily")
                self.statusBar().showMessage(f"{count} customer block(s) restored on the Daily Entry page", 8000)
        else:
            daily.clear_draft()
