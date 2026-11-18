"""Phone Remote page: start/stop the bundled remote server and show the phone link, QR and bot state."""
from __future__ import annotations

import base64
import re

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from .. import paths
from ..remote_control import PORT, RemoteController, find_node, read_env, remote_dir, save_keys
from .widgets import Card, page_header, plain_button, primary_button


class _Worker(QThread):
    done = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.error = ""

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - a crash here must never freeze the page
            self.error = str(exc)
            self.done.emit(None)


class RemotePage(QWidget):
    """``window`` is the MainWindow (service + notifications)."""

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.svc = window.svc
        self.controller = RemoteController(window.svc)
        self._info: dict | None = None
        self._worker: _Worker | None = None
        self._busy = False
        self.setObjectName("page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        layout.addWidget(page_header("Phone Remote",
                                     "Apne phone se is laptop ko control karo — screen, touchpad, apps aur Khata entries."))

        body = QHBoxLayout()
        body.setSpacing(14)
        layout.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(12)

        server = Card("Server")
        self.status_label = QLabel("Checking…")
        self.status_label.setWordWrap(True)
        server.body.addWidget(self.status_label)

        self.url_field = QLineEdit()
        self.url_field.setReadOnly(True)
        self.url_field.setPlaceholderText("Remote start karo — phone link yahan aayega")
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_field, 1)
        copy_btn = plain_button("Copy link")
        copy_btn.clicked.connect(self._copy_url)
        url_row.addWidget(copy_btn)
        open_btn = plain_button("Open here")
        open_btn.clicked.connect(self._open_url)
        url_row.addWidget(open_btn)
        server.body.addLayout(url_row)

        button_row = QHBoxLayout()
        self.start_btn = primary_button("Start remote")
        self.start_btn.clicked.connect(self.start_clicked)
        self.stop_btn = plain_button("Stop")
        self.stop_btn.clicked.connect(self.stop_clicked)
        firewall_btn = plain_button("Firewall allow (admin)")
        firewall_btn.setToolTip("Phone ko same Wi-Fi se connect hone ke liye port " + str(PORT) + " allow karta hai")
        firewall_btn.clicked.connect(self._allow_firewall)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addWidget(firewall_btn)
        button_row.addStretch(1)
        server.body.addLayout(button_row)

        self.autostart_box = QCheckBox("Khata start hone par remote khud start karo")
        self.autostart_box.setChecked(self.controller.autostart())
        self.autostart_box.toggled.connect(self._save_autostart)
        server.body.addWidget(self.autostart_box)

        node = find_node()
        self.node_note = QLabel("Node.js: " + (node or "nahi mila — nodejs.org se install karo (ek baar)."))
        self.node_note.setObjectName("muted")
        self.node_note.setWordWrap(True)
        server.body.addWidget(self.node_note)
        left.addWidget(server)

        setup = Card("Setup — Gemini AI key")
        hint = QLabel("AI chat ke liye aistudio.google.com se API key lo.\n"
                      "Screen, touchpad aur voice commands bina key ke bhi chalte hain.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        setup.body.addWidget(hint)
        form = QFormLayout()
        self.gemini_input = QLineEdit()
        self.gemini_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_input.setPlaceholderText("AIza…")
        self.deepseek_input = QLineEdit()
        self.deepseek_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepseek_input.setPlaceholderText("optional")
        form.addRow("Gemini API key", self.gemini_input)
        form.addRow("DeepSeek key", self.deepseek_input)
        setup.body.addLayout(form)
        save_row = QHBoxLayout()
        save_btn = plain_button("Save keys")
        save_btn.clicked.connect(self._save_keys)
        save_row.addWidget(save_btn)
        self.setup_note = QLabel("")
        self.setup_note.setObjectName("muted")
        self.setup_note.setWordWrap(True)
        save_row.addWidget(self.setup_note, 1)
        setup.body.addLayout(save_row)
        left.addWidget(setup)
        env = read_env()
        self.gemini_input.setText(env.get("GEMINI_API_KEY", ""))
        self.deepseek_input.setText(env.get("DEEPSEEK_API_KEY", ""))

        security = Card("Phone security & entries")
        sec_row = QHBoxLayout()
        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin_input.setPlaceholderText("4-8 digit PIN (blank = off)")
        self.pin_input.setMaximumWidth(220)
        sec_row.addWidget(self.pin_input)
        pin_btn = plain_button("Save PIN")
        pin_btn.clicked.connect(self._save_pin)
        sec_row.addWidget(pin_btn)
        revoke_btn = plain_button("Revoke phones (new link)")
        revoke_btn.setToolTip("Naya token banata hai — purane phone links/APK band ho jayenge")
        revoke_btn.clicked.connect(self._revoke_phones)
        sec_row.addWidget(revoke_btn)
        log_btn = plain_button("Phone log")
        log_btn.clicked.connect(self._open_phone_log)
        sec_row.addWidget(log_btn)
        sec_row.addStretch(1)
        security.body.addLayout(sec_row)
        upi_row = QHBoxLayout()
        self.upi_input = QLineEdit()
        self.upi_input.setPlaceholderText("UPI ID (name@okhdfcbank) — phone pe collect QR ke liye")
        upi_row.addWidget(self.upi_input, 1)
        upi_btn = plain_button("Save UPI")
        upi_btn.clicked.connect(self._save_upi)
        upi_row.addWidget(upi_btn)
        security.body.addLayout(upi_row)
        self.upi_input.setText(self.svc.get_setting("upi_id", ""))
        self.sec_note = QLabel("")
        self.sec_note.setObjectName("muted")
        self.sec_note.setWordWrap(True)
        security.body.addWidget(self.sec_note)
        left.addWidget(security)
        self.pin_input.setText(self.svc.get_setting("phone_pin", ""))

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        left.addWidget(self.note)
        left.addStretch(1)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        qr_card = Card("Phone par scan karo")
        self.qr_label = QLabel("QR ready…")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(200, 200)
        qr_card.body.addWidget(self.qr_label)
        caption = QLabel("Phone aur laptop, dono ek hi Wi-Fi pe hone chahiye.\n"
                         "Phone APK neeche se save karo — pehli baar khulne par yahi URL paste karna.")
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        qr_card.body.addWidget(caption)
        apk_btn = plain_button("Phone APK save karo")
        apk_btn.clicked.connect(self._save_apk)
        qr_card.body.addWidget(apk_btn)
        right.addWidget(qr_card)
        right.addStretch(1)
        body.addLayout(right, 0)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.refresh_now()

    # ------------------------------------------------------------ state
    def refresh_now(self) -> None:
        self._info = self.controller.info()
        self._render()

    def _poll(self) -> None:
        if self._busy:
            return
        if getattr(self.window_, "current_key", "") != "remote":
            return
        self.refresh_now()

    def _render(self) -> None:
        info = self._info
        owned = self.controller.owns_server()
        if info:
            if owned:
                status = "Remote ON — is Khata app ne server start kiya hai."
            else:
                status = "Remote ON — pehle se chal raha hai (standalone VX7 Remote app)."
            self.status_label.setText(status)
            self.url_field.setText(info.get("phoneUrl") or info.get("webToken") or "")
            self._set_qr(info.get("qr"))
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(owned)
            self.note.setText("Stop sirf tab kaam karta hai jab server is app ne start kiya ho." if not owned else "")
        else:
            self.status_label.setText("Remote OFF.")
            self.url_field.setText("")
            self.start_btn.setEnabled(find_node() is not None and not self._busy)
            self.stop_btn.setEnabled(False)
            self._set_qr(None)
            if self.controller.error:
                self.note.setText(self.controller.error)
            elif find_node() is None:
                self.note.setText("Node.js runtime nahi mila — ye build adhoora hai, dobara build karo.")
            else:
                self.note.setText("")
        self.start_btn.setText("Starting…" if self._busy else "Start remote")
        try:
            pend = self.svc.list_pending()
        except Exception:  # noqa: BLE001 - a settings problem must not break the page
            pend = []
        last = pend[-1] if pend else None
        self.sec_note.setText(
            f"Pending entries: {len(pend)}" +
            (f"  •  last: {last.customer_name} — {last.item} ({last.txn_type.title()})" if last else ""))

    def _set_qr(self, data_url: str | None) -> None:
        if not data_url:
            self.qr_label.setText("QR ready…")
            self.qr_label.setPixmap(QPixmap())
            return
        try:
            payload = data_url.split(",", 1)[1]
            pix = QPixmap()
            if pix.loadFromData(base64.b64decode(payload)) and not pix.isNull():
                self.qr_label.setPixmap(pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
                return
        except Exception:  # noqa: BLE001 - a broken QR must not crash the page
            pass
        self.qr_label.setText("QR unavailable")

    # ------------------------------------------------------------ actions
    def _run(self, fn) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._busy = True
        self._render()
        self._worker = _Worker(fn, self)
        self._worker.done.connect(self._finished)
        self._worker.start()

    def _finished(self, info) -> None:
        self._busy = False
        self._info = info
        worker_error = getattr(self._worker, "error", "") if self._worker is not None else ""
        if info is None and (self.controller.error or worker_error):
            self.window_.warn("Phone Remote", self.controller.error or worker_error)
        self._render()

    def start_clicked(self) -> None:
        self.controller.error = ""
        token = self.svc.get_setting("phone_bridge_token", "")
        self._run(lambda: self.controller.start(bridge_token=token))

    def stop_clicked(self) -> None:
        self.controller.stop()
        self.controller.error = ""
        self.refresh_now()

    def start_quiet(self) -> None:
        """Called at app start-up when the autostart setting is on."""
        if self.controller.info():
            return
        if find_node() is None:
            return
        token = self.svc.get_setting("phone_bridge_token", "")
        self._run(lambda: self.controller.start(bridge_token=token))

    def shutdown(self) -> None:
        self._timer.stop()
        self.controller.shutdown()

    def _save_autostart(self, checked: bool) -> None:
        self.controller.set_autostart(bool(checked))

    def _save_keys(self) -> None:
        save_keys(self.gemini_input.text(), self.deepseek_input.text())
        if self.controller.owns_server():
            self.setup_note.setText("Saved — server restart ho raha hai…")
            self._busy = True
            self._render()
            token = self.svc.get_setting("phone_bridge_token", "")

            def restart():
                self.controller.stop()
                return self.controller.start(bridge_token=token)

            self._worker = _Worker(restart, self)
            self._worker.done.connect(self._finished)
            self._worker.start()
        else:
            self.setup_note.setText("Saved. Start remote karne par apply honge.")

    def _save_apk(self) -> None:
        source = remote_dir() / "VX7-Remote.apk"
        if not source.is_file():
            self.window_.warn("Phone APK", "APK is build me bundled nahi hai.")
            return
        target = paths.downloads_dir() / "VX7-Remote.apk"
        try:
            target.write_bytes(source.read_bytes())
        except OSError as exc:
            self.window_.warn("Phone APK", f"APK save nahi hua: {exc}")
            return
        self.window_.statusBar().showMessage(f"APK saved: {target}", 8000)
        self.window_.info("Phone APK", f"APK saved:\n{target}\n\nPhone pe bhejo (Telegram Saved Messages sabse easy), phir install karo.")

    def _allow_firewall(self) -> None:
        import subprocess
        rule = f'netsh advfirewall firewall add rule name="VX7 Khata Phone" dir=in action=allow protocol=TCP localport={PORT}'
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 f"Start-Process cmd.exe -ArgumentList '/c {rule}' -Verb RunAs -WindowStyle Hidden"])
        except OSError as exc:
            self.window_.warn("Firewall", f"Firewall rule nahi bana: {exc}")
            return
        self.window_.statusBar().showMessage(
            "UAC prompt pe 'Yes' dabao — phone ke liye firewall khul jayega", 8000)

    def _revoke_phones(self) -> None:
        if not self.window_.confirm(
                "Revoke phones",
                "Naya phone link ban jayega aur purane sab links/APK band ho jayenge.\n\n"
                "Phone pe naya QR scan karna padega. Continue?", default_no=True):
            return
        import urllib.request
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{PORT}/api/desktop/regenerate-token", method="POST")
            with urllib.request.urlopen(request, timeout=4) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001
            self.window_.warn("Revoke phones", f"Token rotate nahi hua: {exc}")
            return
        self.window_.statusBar().showMessage("Naya phone token ban gaya — naya QR scan karo", 8000)
        self.refresh_now()

    def _open_phone_log(self) -> None:
        import os
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        path = os.path.join(os.environ.get("APPDATA", ""), "vx7-remote", "phone-access.log")
        if not os.path.isfile(path):
            self.window_.info("Phone log", "Abhi koi phone connection log nahi hai.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _save_upi(self) -> None:
        self.svc.set_setting("upi_id", self.upi_input.text().strip())
        self.window_.statusBar().showMessage("UPI ID saved — phone pe Collect QR ab kaam karega", 5000)

    def _save_pin(self) -> None:
        pin = self.pin_input.text().strip()
        if pin and not re.fullmatch(r"\d{4,8}", pin):
            self.window_.warn("Phone PIN", "4-8 digits ka PIN rakho (ya blank rakho = off).")
            return
        self.svc.set_setting("phone_pin", pin)
        self.window_.statusBar().showMessage("Phone PIN saved" if pin else "Phone PIN removed", 5000)

    def _copy_url(self) -> None:
        url = self.url_field.text().strip()
        if url:
            QApplication.clipboard().setText(url)
            self.window_.statusBar().showMessage("Phone link copied", 4000)

    def _open_url(self) -> None:
        url = self.url_field.text().strip()
        if not url:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
