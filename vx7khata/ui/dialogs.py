"""Dialogs: customer, page setup (printing), quick payment, entry edit, reminder, audit details, PIN setup, lock screen, PIN recovery."""
from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QCheckBox, QGridLayout, QGroupBox, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout,
)

from .. import APP_NAME, insights, money, pagesetup, security, viewmodel, voice
from ..labels import JAMA_LABEL, QUICK_PAYMENT_ITEM
from ..service import AuditEntry, CreditLimitError, Customer, KhataError, KhataService, Transaction
from .widgets import DateField, EntryForm, plain_button, populate_customer_combo, primary_button, set_kind


def accentuate(buttons: QDialogButtonBox) -> QDialogButtonBox:
    """Paint the dialog's own OK/Save button like the blue buttons used on the pages."""
    for button in buttons.buttons():
        if buttons.buttonRole(button) in (QDialogButtonBox.ButtonRole.AcceptRole,
                                          QDialogButtonBox.ButtonRole.YesRole):
            button.setObjectName("primary")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
    return buttons


class CustomerDialog(QDialog):
    def __init__(self, service: KhataService, customer: Customer | None = None, parent=None, name: str = ""):
        super().__init__(parent)
        self.service = service
        self.customer = customer
        self.saved: Customer | None = None
        self.setWindowTitle("Edit customer" if customer else "Add customer")
        self.setMinimumWidth(460)

        self.name = QLineEdit(customer.name if customer else name)  # ``name`` pre-fills a new customer
        self.name.setMaxLength(120)
        self.mobile = QLineEdit(customer.mobile if customer else "")
        self.mobile.setPlaceholderText("optional")
        self.address = QPlainTextEdit(customer.address if customer else "")
        self.address.setFixedHeight(48)
        self.notes = QPlainTextEdit(customer.notes if customer else "")
        self.notes.setFixedHeight(48)
        self.limit = QLineEdit()
        self.limit.setPlaceholderText("optional – leave empty for no limit")
        if customer and customer.credit_limit_paise:
            from .. import money
            self.limit.setText(money.plain_amount(customer.credit_limit_paise))

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("Name *", self.name)
        form.addRow("Mobile", self.mobile)
        form.addRow("Address", self.address)
        form.addRow("Credit limit (₹)", self.limit)
        form.addRow("Notes", self.notes)

        buttons = accentuate(QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.name.setFocus()

    def _save(self) -> None:
        args = (self.name.text(), self.mobile.text(), self.address.toPlainText(), self.notes.toPlainText())
        try:
            if self.customer:
                self.saved = self.service.update_customer(self.customer.id, *args, credit_limit=self.limit.text())
            else:
                self.saved = self.service.add_customer(*args, credit_limit=self.limit.text())
        except KhataError as exc:
            QMessageBox.warning(self, "Cannot save customer", str(exc))
            return
        self.accept()


class PageSetupDialog(QDialog):
    """Paper size (every size Qt knows, or a custom width x height in mm) and Portrait / Landscape for printing."""

    def __init__(self, initial: "pagesetup.PageSetup", parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QPageSize

        self.setWindowTitle("Page setup and print options")
        self.setMinimumWidth(420)
        self.result_setup: "pagesetup.PageSetup" = initial

        self.size_combo = QComboBox()
        seen: set[int] = set()
        for pid in QPageSize.PageSizeId:
            if pid == QPageSize.PageSizeId.Custom or pid.value in seen:
                continue
            seen.add(pid.value)
            self.size_combo.addItem(QPageSize(pid).name(), pid.name)
        self.size_combo.addItem("Custom size...", pagesetup.CUSTOM)

        index = self.size_combo.findData(initial.size)
        if index < 0:
            index = self.size_combo.findData(pagesetup.DEFAULT_SIZE)
        self.size_combo.setCurrentIndex(max(0, index))

        self.portrait = QRadioButton("Portrait")
        self.landscape = QRadioButton("Landscape")
        (self.landscape if initial.orientation == pagesetup.LANDSCAPE else self.portrait).setChecked(True)
        orient_row = QHBoxLayout()
        orient_row.addWidget(self.portrait)
        orient_row.addWidget(self.landscape)
        orient_row.addStretch(1)

        self.width = self._mm_box(initial.custom_w_mm)
        self.height = self._mm_box(initial.custom_h_mm)
        self.custom_label = QLabel("Custom size (mm)")
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("Width"))
        custom_row.addWidget(self.width)
        custom_row.addWidget(QLabel("x  Height"))
        custom_row.addWidget(self.height)
        custom_row.addStretch(1)
        self.custom_hint = QLabel(f"Allowed: {pagesetup.MIN_MM:g} to {pagesetup.MAX_MM:g} mm each way.")
        self.custom_hint.setObjectName("muted")

        form = QFormLayout()
        form.addRow("Paper size", self.size_combo)
        form.addRow("Orientation", orient_row)
        form.addRow(self.custom_label, custom_row)
        form.addRow("", self.custom_hint)

        self.item_boxes: dict[str, QCheckBox] = {}
        columns_box = QGroupBox("Columns to print")
        grid = QGridLayout(columns_box)
        for i, key in enumerate(pagesetup.COLUMNS):
            box = QCheckBox(pagesetup.COLUMN_LABELS[key])
            box.setChecked(key in initial.items)
            self.item_boxes[key] = box
            grid.addWidget(box, i // 2, i % 2)
        extras_box = QGroupBox("Also print")
        extras = QVBoxLayout(extras_box)
        for key in pagesetup.SECTIONS:
            box = QCheckBox(pagesetup.SECTION_LABELS[key])
            box.setChecked(key in initial.items)
            self.item_boxes[key] = box
            extras.addWidget(box)
        balance_hint = QLabel("Balance column also controls the opening / closing balance lines.")
        balance_hint.setObjectName("muted")
        balance_hint.setWordWrap(True)

        buttons = accentuate(QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(columns_box)
        layout.addWidget(balance_hint)
        layout.addWidget(extras_box)
        layout.addWidget(buttons)

        self.size_combo.currentIndexChanged.connect(self._sync_custom)
        self._sync_custom()

    @staticmethod
    def _mm_box(value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(1)
        box.setRange(pagesetup.MIN_MM, pagesetup.MAX_MM)
        box.setSuffix(" mm")
        box.setValue(min(pagesetup.MAX_MM, max(pagesetup.MIN_MM, value)))
        return box

    def _sync_custom(self) -> None:
        custom = self.size_combo.currentData() == pagesetup.CUSTOM
        for w in (self.custom_label, self.width, self.height, self.custom_hint):
            w.setVisible(custom)
        self.adjustSize()

    def _accept(self) -> None:
        setup = pagesetup.PageSetup(
            self.size_combo.currentData(),
            pagesetup.LANDSCAPE if self.landscape.isChecked() else pagesetup.PORTRAIT,
            self.width.value(), self.height.value(),
            tuple(k for k in pagesetup.ALL_ITEMS if self.item_boxes[k].isChecked()),
        )
        try:
            self.result_setup = pagesetup.validate(setup)
        except KhataError as exc:
            QMessageBox.warning(self, "Page setup", str(exc))
            return
        self.accept()


class QuickPaymentDialog(QDialog):
    """Fastest way to record a payment: pick customer, type the amount, press Enter. No item needed.

    ``save_fn(customer_id, values)`` does the real saving (so the main window can ask about duplicates);
    it returns the saved Transaction, or None if the user backed out. A KhataError keeps this dialog open.
    """

    def __init__(self, service: KhataService, save_fn, customer_id: int | None = None, parent=None):
        super().__init__(parent)
        self.service = service
        self._save_fn = save_fn
        self.saved: Transaction | None = None
        self.setWindowTitle(f"Quick payment \u2013 {JAMA_LABEL}")
        self.setMinimumWidth(460)

        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        populate_customer_combo(self.customer, service.list_customers(), keep_id=customer_id)

        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.amount.setStyleSheet("font-size: 20px; padding: 6px;")
        self.date = DateField()
        self.note = QLineEdit()
        self.note.setPlaceholderText("optional (e.g. UPI, cash, cheque no.)")
        self.note.setMaxLength(200)
        self.balance_label = QLabel("")
        self.balance_label.setObjectName("muted")

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("Customer", self.customer)
        form.addRow("Amount (\u20b9)", self.amount)
        form.addRow("Date", self.date)
        form.addRow("Note", self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = primary_button("Save payment")
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.save_button.clicked.connect(self._save)
        self.amount.returnPressed.connect(self._save)
        self.note.returnPressed.connect(self._save)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.balance_label)
        layout.addWidget(buttons)
        self.customer.currentIndexChanged.connect(self._update_balance)
        self.customer.editTextChanged.connect(self._update_balance)
        self._update_balance()
        self.amount.setFocus()

    def customer_id(self) -> int:
        text = self.customer.currentText().strip()
        if not text:
            raise KhataError("Select a customer")
        index = self.customer.findText(text, Qt.MatchFlag.MatchFixedString)
        if index < 0:
            raise KhataError(f"No customer named '{text}'. Pick one from the list.")
        return self.customer.itemData(index)

    def values(self) -> dict:
        return {
            "txn_type": "JAMA",
            "txn_date": self.date.text_dmy(),
            "item": QUICK_PAYMENT_ITEM,
            "amount": self.amount.text(),
            "notes": self.note.text(),
        }

    def _update_balance(self, *_args) -> None:
        try:
            balance = self.service.customer_balance(self.customer_id())
        except KhataError:
            self.balance_label.setText("")
            return
        self.balance_label.setText(f"Current balance: {money.format_inr(balance)}")

    def _save(self) -> None:
        self.save_button.setEnabled(False)
        try:
            try:
                txn = self._save_fn(self.customer_id(), self.values())
            except KhataError as exc:
                QMessageBox.warning(self, "Cannot save payment", str(exc))
                return
            if txn is None:
                return
            self.saved = txn
            self.accept()
        finally:
            self.save_button.setEnabled(True)


class EditEntryDialog(QDialog):
    """Edit date, amount, type, item description, notes, due date (and qty/rate) of a saved entry."""

    def __init__(self, service: KhataService, txn: Transaction, customer_name: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.txn = txn
        self.saved: Transaction | None = None
        self.setWindowTitle(f"Edit entry – {customer_name}")
        self.setMinimumWidth(580)

        self.form = EntryForm(with_customer=False)
        self.form.set_values(txn)

        buttons = accentuate(QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addWidget(buttons)

    def _save(self) -> None:
        values = self.form.values()
        allow_over_limit = False
        while True:
            try:
                self.saved = self.service.update_transaction(self.txn.id, allow_over_limit=allow_over_limit, **values)
                break
            except CreditLimitError as exc:
                answer = QMessageBox.question(self, "Credit limit exceeded", f"{exc}\n\nSave this change anyway?")
                if allow_over_limit or answer != QMessageBox.StandardButton.Yes:
                    return
                allow_over_limit = True
            except KhataError as exc:
                QMessageBox.warning(self, "Cannot save entry", str(exc))
                return
        self.accept()


class ReminderDialog(QDialog):
    """Shows the reminder text for a customer; copy it or open it in WhatsApp (needs internet and WhatsApp)."""

    def __init__(self, service: KhataService, customer_id: int, parent=None):
        super().__init__(parent)
        self.service = service
        self.customer = service.get_customer(customer_id)
        self.setWindowTitle(f"Payment reminder – {self.customer.name}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        note = QLabel("Edit the message if you like, then copy it or send it on WhatsApp. "
                      "Nothing is sent by this app itself.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        self.text = QPlainTextEdit(insights.reminder_message(service, customer_id))
        self.text.setMinimumHeight(150)
        layout.addWidget(self.text)

        buttons = QHBoxLayout()
        copy = plain_button("Copy text")
        copy.clicked.connect(self._copy)
        self.whatsapp = primary_button("Open in WhatsApp")
        self.whatsapp.clicked.connect(self._open_whatsapp)
        if not insights.whatsapp_url(self.customer.mobile, "x", self._country_code()):
            self.whatsapp.setEnabled(False)
            self.whatsapp.setToolTip("This customer has no usable mobile number")
        close = plain_button("Close")
        close.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(copy)
        buttons.addWidget(self.whatsapp)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _country_code(self) -> str:
        return self.service.get_setting(insights.SETTING_COUNTRY_CODE, insights.DEFAULT_COUNTRY_CODE)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())

    def _open_whatsapp(self) -> None:
        url = insights.whatsapp_url(self.customer.mobile, self.text.toPlainText(), self._country_code())
        if url:
            QDesktopServices.openUrl(QUrl(url))


class AuditDetailDialog(QDialog):
    def __init__(self, entry: AuditEntry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(viewmodel.action_label(entry.action))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        when = QLabel(viewmodel.timestamp_text(entry.at))
        when.setObjectName("muted")
        layout.addWidget(when)
        summary = QLabel(entry.summary)
        summary.setWordWrap(True)
        summary.setObjectName("h2")
        layout.addWidget(summary)
        lines = viewmodel.audit_details_lines(entry)
        if lines:
            body = QLabel("\n".join(lines))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


def _show_recovery_code(parent, code: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("Save your recovery code")
    box.setText("Write this recovery code down and keep it somewhere safe.\n"
                "It is the ONLY way to reset a forgotten PIN, and it is shown just once.")
    box.setInformativeText(code)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    copy = box.addButton("Copy code", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
    if box.clickedButton() is copy:
        QGuiApplication.clipboard().setText(code)


class PinSetupDialog(QDialog):
    """Set a new PIN, or change the existing one."""

    def __init__(self, service: KhataService, parent=None):
        super().__init__(parent)
        self.service = service
        self.changing = security.has_pin(service)
        self.setWindowTitle("Change PIN" if self.changing else "Set PIN")
        self.setMinimumWidth(380)
        form = QFormLayout()
        self.current = QLineEdit()
        self.current.setEchoMode(QLineEdit.EchoMode.Password)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.new.setMaxLength(security.MAX_PIN_LEN)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setMaxLength(security.MAX_PIN_LEN)
        if self.changing:
            form.addRow("Current PIN", self.current)
        form.addRow(f"New PIN ({security.MIN_PIN_LEN}-{security.MAX_PIN_LEN} digits)", self.new)
        form.addRow("Repeat new PIN", self.confirm)
        note = QLabel("The PIN locks the app window. It does not encrypt the data file.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        buttons = accentuate(QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _save(self) -> None:
        if self.new.text() != self.confirm.text():
            QMessageBox.warning(self, "PIN", "The two new PINs do not match.")
            return
        try:
            code = security.set_pin(self.service, self.new.text(), self.current.text() if self.changing else None)
        except KhataError as exc:
            QMessageBox.warning(self, "PIN", str(exc))
            return
        self.accept()
        _show_recovery_code(self.parent(), code)


class RecoveryDialog(QDialog):
    def __init__(self, service: KhataService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Reset PIN with recovery code")
        self.setMinimumWidth(400)
        form = QFormLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("XXXX-XXXX-XXXX")
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.new.setMaxLength(security.MAX_PIN_LEN)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setMaxLength(security.MAX_PIN_LEN)
        form.addRow("Recovery code", self.code)
        form.addRow("New PIN", self.new)
        form.addRow("Repeat new PIN", self.confirm)
        buttons = accentuate(QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _save(self) -> None:
        if self.new.text() != self.confirm.text():
            QMessageBox.warning(self, "PIN", "The two new PINs do not match.")
            return
        try:
            new_code = security.reset_pin_with_recovery(self.service, self.code.text(), self.new.text())
        except KhataError as exc:
            QMessageBox.warning(self, "PIN", str(exc))
            return
        self.accept()
        _show_recovery_code(self.parent(), new_code)


class LockDialog(QDialog):
    """Modal PIN screen. Accepted = unlocked; the 'Exit' button rejects (the caller then quits the app)."""

    def __init__(self, service: KhataService, guard: security.PinGuard, parent=None):
        super().__init__(parent)
        self.service = service
        self.guard = guard
        self.setWindowTitle(APP_NAME)
        self.setModal(True)
        self.setMinimumWidth(340)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        layout.addWidget(title)
        hint = QLabel("Enter your PIN to continue")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setMaxLength(security.MAX_PIN_LEN)
        self.pin.setPlaceholderText("PIN")
        self.pin.returnPressed.connect(self._try)
        layout.addWidget(self.pin)
        self.status = QLabel("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.unlock = primary_button("Unlock")
        self.unlock.clicked.connect(self._try)
        forgot = plain_button("Forgot PIN?")
        forgot.clicked.connect(self._forgot)
        quit_button = plain_button("Exit")
        quit_button.clicked.connect(lambda: QDialog.reject(self))  # real reject; Esc is blocked below
        row.addWidget(self.unlock)
        row.addWidget(forgot)
        row.addStretch(1)
        row.addWidget(quit_button)
        layout.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.pin.setFocus()

    def reject(self) -> None:  # Esc must not bypass the lock
        pass

    def _show_status(self, message: str) -> None:
        """The error line only becomes a red chip while it actually says something."""
        self.status.setText(message)
        set_kind(self.status, "danger" if message else "")

    def _tick(self) -> None:
        seconds = self.guard.seconds_locked()
        if seconds > 0:
            self._show_status(f"Too many wrong attempts. Try again in {seconds} s.")
        else:
            self._timer.stop()
            self._show_status("")
            self.unlock.setEnabled(True)
            self.pin.setEnabled(True)
            self.pin.setFocus()

    def _try(self) -> None:
        if self.guard.attempt(self.pin.text()):
            voice.announce("unlocked")
            self.accept()
            return
        self.pin.clear()
        if self.guard.seconds_locked() > 0:
            voice.announce("pin_locked")
            self.unlock.setEnabled(False)
            self.pin.setEnabled(False)
            self._timer.start()
            self._tick()
        else:
            voice.announce("pin_wrong")
            self._show_status("Wrong PIN")

    def _forgot(self) -> None:
        dlg = RecoveryDialog(self.service, self)
        if dlg.exec():
            self.guard.failures = 0
            self.guard.locked_until = None
            self.accept()
