"""Application bootstrap."""
from __future__ import annotations

import sys

from . import APP_NAME, paths


def _start_assistant(service):
    """Bring up the offline voice assistant and make it the one the whole app announces through.

    It never touches the network and never raises: with no speech engine, no voice model or no sound card it
    simply stays silent and says so through ``status()``.
    """
    from . import voice

    manager = voice.VoiceManager(service)
    voice.install(manager)
    manager.start()
    return manager


def run() -> int:
    import os

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    from . import db, security, voice
    from .service import KhataService
    from .ui import theme
    from .ui.dialogs import LockDialog
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    logo = paths.asset_path("logo.png")
    if logo.is_file():
        app.setWindowIcon(QIcon(str(logo)))

    splash = None   # welcome screen while the data file opens (skipped silently if the image is missing)
    bg = paths.asset_path("welcome_bg.jpg")
    if bg.is_file():
        pix = QPixmap(str(bg))
        if not pix.isNull():
            splash = QSplashScreen(pix.scaled(900, 506, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
            splash.show()
            splash.showMessage("Loading your khata…", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                               Qt.GlobalColor.white)
            app.processEvents()
    if os.name == "nt":
        app.setFont(QFont("Segoe UI", 10))

    try:
        service = KhataService(paths.db_path())
    except (db.DatabaseError, OSError) as exc:
        app.setStyleSheet(theme.build_stylesheet("light"))
        if splash is not None:
            splash.close()
        assistant = _start_assistant(None)      # no data file, so built-in preferences
        voice.announce("db_error")
        QMessageBox.critical(None, APP_NAME, f"The data file could not be opened:\n\n{exc}\n\n{paths.db_path()}")
        assistant.shutdown()
        return 1

    name = service.get_setting("theme", "light")
    theme.set_current(name)
    app.setStyleSheet(theme.build_stylesheet(name))

    assistant = _start_assistant(service)

    if security.has_pin(service):  # PIN screen comes before any data is shown
        if splash is not None:
            splash.close()
            splash = None
        if not LockDialog(service, security.PinGuard(service)).exec():
            assistant.shutdown()
            service.close()
            return 0

    window = MainWindow(service)
    window.show()
    voice.announce("app_ready")
    if splash is not None:
        QTimer.singleShot(1200, splash.close)   # keep the welcome screen visible for a moment
    window.run_startup_tasks()
    code = app.exec()
    assistant.shutdown()
    service.close()
    return code
