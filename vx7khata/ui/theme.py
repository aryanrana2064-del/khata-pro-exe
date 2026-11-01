"""Colour palettes and the application stylesheet - one central place for every colour and corner radius.

The light palette is the *Aurora White* look: an ice-blue aurora window (``#F8FBFF -> #EAF4FF -> #DCEBFF`` with a
soft white/cyan bloom), frosted semi-transparent white panels, a glass sidebar, blue accents and rounded controls.
``dark`` is the same token structure in night colours, so every screen keeps the same shape in either theme.

Two rules keep this maintainable:

* Anything the style sheet paints may be a Qt gradient or ``rgba(...)`` expression - but a token that Python reads
  back (``text``, ``muted``, ``border``, ``hover``, ``danger``, ``success``, ``warning``, ``chart_*``, ``bar_*``) must
  stay a plain ``#rrggbb`` because :class:`QColor` cannot parse those expressions.
* Qt style sheets have no backdrop blur or ``box-shadow``. Frosted panels are therefore translucent paints over the
  window gradient (the graceful fallback where ``backdrop-filter`` has no equivalent), and the shadows come from
  :func:`vx7khata.ui.widgets.glass_shadow` with :func:`shadow_spec`.
"""
from __future__ import annotations

import re

# The official Aurora White palette. Values are written once here and referenced by every rule below.
AURORA = {
    "primary_blue": "#2563EB", "secondary_blue": "#3B82F6", "accent_blue": "#0EA5E9",
    "deep_navy": "#172554", "aurora_blue": "#60A5FA", "active_blue": "#1D4ED8",
    "main_bg": "#F3F7FC", "secondary_bg": "#EAF2FC",
    "grad_start": "#F8FBFF", "grad_mid": "#EAF4FF", "grad_end": "#DCEBFF",
    "text": "#0F172A", "text_secondary": "#475569", "text_muted": "#64748B", "text_disabled": "#94A3B8",
    "success": "#16A34A", "success_bg": "#DCFCE7", "warning": "#D97706", "warning_bg": "#FEF3C7",
    "danger": "#DC2626", "danger_bg": "#FEE2E2", "info": "#2563EB", "info_bg": "#DBEAFE",
    "input_border": "#D8E4F2", "input_focus": "#3B82F6", "chart_grid": "#E2E8F0",
    "debit": "#F87171", "credit": "#34D399",
}

PALETTES: dict[str, dict[str, str]] = {
    # ---- Aurora White: frosted white glass on a soft ice-blue sky
    "light": {
        "bg": "#F3F7FC", "surface": "#FFFFFF", "border": "#D8E4F2", "text": "#0F172A", "muted": "#64748B",
        "navy": "#172554", "secondary_text": "#475569", "disabled_text": "#94A3B8",
        "primary": "#2563EB", "primary_hover": "#3B82F6", "primary_text": "#FFFFFF", "accent": "#0EA5E9",
        "sidebar": "#F5F9FF", "sidebar_text": "#172554", "sidebar_active": "#DBEAFE", "sidebar_muted": "#64748B",
        "danger": "#DC2626", "danger_bg": "#FEE2E2",
        "success": "#16A34A", "success_bg": "#DCFCE7",
        "warning": "#D97706", "warning_bg": "#FEF3C7",
        "info": "#2563EB", "info_bg": "#DBEAFE",
        "row_alt": "#F5F9FF", "selection": "#DBEAFE", "input_bg": "#FFFFFF",
        "input_border": "#D8E4F2", "focus": "#3B82F6",
        "hover": "#DBEAFE",
        "bar_udhaar": "#F87171", "bar_jama": "#34D399",
        "chart_grid": "#E2E8F0", "chart_axis": "#64748B",
        # ---- paints: gradients and translucent whites (style sheet only, never handed to QColor)
        "bg_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1," \
                       " stop:0 #F8FBFF, stop:0.45 #EAF4FF, stop:1 #DCEBFF)",
        # The "selected areas" bloom: a white->ice-blue->transparent radial laid over the window gradient.
        "aurora_glow": "qradialgradient(cx:0.14, cy:0.0, radius:1.2, fx:0.14, fy:0.0," \
                       " stop:0 rgba(255, 255, 255, 235), stop:0.4 rgba(233, 243, 255, 130)," \
                       " stop:0.75 rgba(219, 234, 254, 60), stop:1 rgba(220, 235, 255, 0))",
        "glass": "rgba(255, 255, 255, 173)",            # primary glass 0.68
        "glass_secondary": "rgba(255, 255, 255, 122)",  # secondary glass 0.48
        "glass_strong": "rgba(255, 255, 255, 209)",     # strong glass 0.82
        "glass_hover": "rgba(255, 255, 255, 235)",      # glass hover 0.92
        "glass_input": "rgba(255, 255, 255, 205)",
        "glass_alt": "rgba(255, 255, 255, 122)",
        "glass_row": "rgba(255, 255, 255, 140)",        # table row 0.55
        "glass_row_hover": "rgba(219, 234, 254, 115)",  # table hover 0.45
        "glass_header": "rgba(219, 234, 254, 166)",     # table header 0.65
        "glass_edge": "rgba(255, 255, 255, 217)",       # glass border 0.85
        "glass_blue_edge": "rgba(37, 99, 235, 36)",     # blue glass border 0.14
        "glass_line": "rgba(148, 163, 184, 56)",        # divider 0.22
        "glass_selection": "rgba(191, 219, 254, 200)",
        "popup": "#FFFFFF",
        "sidebar_gradient": "qradialgradient(cx:0.5, cy:0.0, radius:1.05, fx:0.5, fy:0.0," \
                            " stop:0 rgba(255, 255, 255, 235), stop:0.55 rgba(243, 247, 252, 205)," \
                            " stop:1 rgba(220, 235, 255, 180))",
        "nav_hover": "rgba(255, 255, 255, 200)",
        "nav_active": "qlineargradient(x1:0, y1:0, x2:1, y2:0," \
                      " stop:0 rgba(219, 234, 254, 240), stop:1 rgba(147, 197, 253, 150))",
        "side_edge": "rgba(37, 99, 235, 36)", "accent_bar": "#2563EB",
        "primary_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2563EB, stop:1 #0EA5E9)",
        "primary_gradient_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3B82F6, stop:1 #0EA5E9)",
        "danger_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #DC2626, stop:1 #B91C1C)",
        "success_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #16A34A, stop:1 #15803D)",
        "brand_text": "#172554", "nav_active_text": "#1D4ED8",
        "radius_card": "16px", "radius_ctrl": "10px", "radius_table": "14px",
    },
    # ---- the same glass structure, night colours
    "dark": {
        "bg": "#0F1522", "surface": "#172033", "border": "#2B3752", "text": "#EEF1F8", "muted": "#A3AFC9",
        "navy": "#EEF1F8", "secondary_text": "#C4CEE6", "disabled_text": "#6B7A99",
        "primary": "#7B93FF", "primary_hover": "#93A7FF", "primary_text": "#0B1020", "accent": "#38BDF8",
        "sidebar": "#0A0F1D", "sidebar_text": "#C4CEE6", "sidebar_active": "#1C2747", "sidebar_muted": "#8290B2",
        "danger": "#FF8585", "danger_bg": "#3A1620",
        "success": "#52D89B", "success_bg": "#123326",
        "warning": "#F3BB55", "warning_bg": "#3A2B12",
        "info": "#7B93FF", "info_bg": "#1B2748",
        "row_alt": "#141C2D", "selection": "#2A3E78", "input_bg": "#0F1626",
        "input_border": "#2B3752", "focus": "#7B93FF",
        "hover": "#1E2940",
        "bar_udhaar": "#F87171", "bar_jama": "#34D399",
        "chart_grid": "#243049", "chart_axis": "#A3AFC9",
        # ---- paints
        "bg_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1," \
                       " stop:0 #101A2C, stop:0.45 #0D1424, stop:1 #0A0F1D)",
        "aurora_glow": "qradialgradient(cx:0.14, cy:0.0, radius:1.2, fx:0.14, fy:0.0," \
                       " stop:0 rgba(123, 147, 255, 40), stop:0.5 rgba(56, 189, 248, 18), stop:1 rgba(10, 15, 29, 0))",
        "glass": "qlineargradient(x1:0, y1:0, x2:0, y2:1," \
                 " stop:0 rgba(32, 44, 70, 235), stop:1 rgba(22, 31, 51, 220))",
        "glass_secondary": "rgba(26, 36, 58, 160)",
        "glass_strong": "rgba(38, 52, 82, 240)",
        "glass_hover": "rgba(48, 64, 98, 245)",
        "glass_input": "rgba(15, 22, 38, 235)",
        "glass_alt": "rgba(26, 36, 58, 225)",
        "glass_row": "rgba(23, 32, 51, 225)",
        "glass_row_hover": "rgba(42, 62, 120, 150)",
        "glass_header": "rgba(30, 42, 66, 235)",
        "glass_edge": "rgba(64, 82, 120, 200)",
        "glass_blue_edge": "rgba(123, 147, 255, 60)",
        "glass_line": "rgba(88, 108, 152, 200)",
        "glass_selection": "rgba(48, 70, 128, 235)",
        "popup": "#1C2639",
        "sidebar_gradient": "qradialgradient(cx:0.5, cy:0.0, radius:1.05, fx:0.5, fy:0.0," \
                            " stop:0 rgba(24, 34, 58, 245), stop:1 rgba(9, 14, 27, 230))",
        "nav_hover": "rgba(30, 43, 76, 235)",
        "nav_active": "qlineargradient(x1:0, y1:0, x2:1, y2:0," \
                      " stop:0 rgba(38, 55, 97, 245), stop:1 rgba(56, 82, 150, 200))",
        "side_edge": "rgba(123, 147, 255, 60)", "accent_bar": "#7B93FF",
        "primary_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8AA0FF, stop:1 #6B85F5)",
        "primary_gradient_hover": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #9CADFF, stop:1 #7D95FF)",
        "danger_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #E0575B, stop:1 #B91C1C)",
        "success_gradient": "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34D399, stop:1 #059669)",
        "brand_text": "#FFFFFF", "nav_active_text": "#FFFFFF",
        "radius_card": "16px", "radius_ctrl": "10px", "radius_table": "14px",
    },
}

# Card shadows cannot be expressed in a style sheet; widgets.py turns these into QGraphicsDropShadowEffect values.
# The light entry is the specified `0 8px 32px rgba(37,99,235,0.08)`.
SHADOWS: dict[str, dict[str, int | str]] = {
    "light": {"color": "#2563EB", "alpha": 22, "blur": 32, "offset_y": 8},
    "dark": {"color": "#000000", "alpha": 96, "blur": 28, "offset_y": 8},
}

_current = "light"


def current_name() -> str:
    return _current


def current() -> dict[str, str]:
    return PALETTES[_current]


def set_current(name: str) -> None:
    global _current
    _current = name if name in PALETTES else "light"


def shadow_spec() -> dict[str, int | str]:
    """The shadow of the current palette: ``{color, alpha, blur, offset_y}``."""
    return SHADOWS.get(_current, SHADOWS["light"])


_TEMPLATE = """
* { font-size: 14px; font-family: "Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif; }

/* ---- window, aurora backdrop and pages ----------------------------------------------------------- */
QMainWindow, QDialog { background: @bg_gradient@; color: @text@; }
QStackedWidget { background: transparent; }
QWidget#page { background: @aurora_glow@; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QLabel { color: @text@; background: transparent; }
QLabel#h1 { font-size: 24px; font-weight: 700; color: @navy@; }
QLabel#h2 { font-size: 16px; font-weight: 600; }
QLabel#muted, QLabel[muted="true"] { color: @muted@; }
QLabel#emptyState { color: @muted@; font-style: italic; }
QLabel#kpiValue { font-size: 26px; font-weight: 700; color: @navy@; }
QLabel#kpiTitle { color: @muted@; font-size: 12px; font-weight: 700; letter-spacing: 1px; }
QLabel#kpiCaption { color: @muted@; font-size: 12px; }
QLabel[kind="danger"] { color: @danger@; background: @danger_bg@; border-radius: 7px; padding: 2px 8px; font-weight: 600; }
QLabel[kind="success"] { color: @success@; background: @success_bg@; border-radius: 7px; padding: 2px 8px; font-weight: 600; }
QLabel[kind="warning"] { color: @warning@; background: @warning_bg@; border-radius: 7px; padding: 2px 8px; font-weight: 600; }
QLabel[kind="info"] { color: @info@; background: @info_bg@; border-radius: 7px; padding: 2px 8px; font-weight: 600; }
QLabel#kpiIcon { background: @info_bg@; border: 1px solid @glass_blue_edge@; border-radius: 11px; }

/* ---- sidebar: translucent glass, blue edge, soft blue gradient on the current page ---------------- */
QFrame#sidebar { background: @sidebar_gradient@; border-right: 1px solid @side_edge@; }
QLabel#brand { color: @brand_text@; font-size: 18px; font-weight: 800; letter-spacing: 1px; background: transparent; }
QLabel#brandSub { color: @sidebar_muted@; font-size: 12px; background: transparent; }
QLabel#sideNote { color: @sidebar_muted@; font-size: 12px; background: transparent; }
QFrame#sideDivider { background: @glass_line@; border: none; }
QPushButton#nav { background: transparent; color: @sidebar_text@; border: none; border-left: 4px solid transparent;
                  border-radius: @radius_ctrl@; text-align: left; padding: 10px 12px; font-size: 14px; }
QPushButton#nav:hover { background: @nav_hover@; color: @navy@; }
QPushButton#nav:checked { background: @nav_active@; color: @nav_active_text@; font-weight: 700;
                          border-left: 4px solid @accent_bar@; }
QPushButton#sideTool { background: @nav_hover@; color: @sidebar_text@; border: 1px solid @glass_blue_edge@;
                       border-radius: @radius_ctrl@; padding: 8px 10px; }
QPushButton#sideTool:hover { background: @nav_active@; color: @nav_active_text@; border: 1px solid @primary@; }

/* ---- cards and group boxes ----------------------------------------------------------------------- */
QFrame#card { background: @glass@; border: 1px solid @glass_edge@; border-radius: @radius_card@; }
QFrame#card:hover { background: @glass_hover@; border: 1px solid @glass_blue_edge@; }
QFrame#card QLabel { background: transparent; }
QGroupBox { background: @glass@; border: 1px solid @glass_edge@; border-radius: @radius_card@; margin-top: 16px;
            padding: 16px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: @muted@; }

/* ---- daily entry: the waiting-to-be-saved panel on the right -------------------------------------- */
QSplitter#dailySplit::handle { background: transparent; border-radius: 4px; }
QSplitter#dailySplit::handle:hover { background: @glass_line@; }
QFrame#sidePanel { background: @glass_secondary@; border: 1px solid @glass_edge@; border-radius: @radius_card@; }
QFrame#sidePanel QLabel { background: transparent; }
QFrame#panelRail { background: @glass_alt@; border: 1px solid @glass_edge@; border-radius: @radius_card@; }
QLabel#railText { color: @muted@; font-size: 12px; font-weight: 700; letter-spacing: 1px; }
QPushButton#panelToggle { background: @glass_hover@; color: @text@; border: 1px solid @glass_line@;
                          border-radius: 8px; padding: 0; min-height: 0; font-weight: 700; }
QPushButton#panelToggle:hover { background: @glass_selection@; border: 1px solid @primary@; }
QFrame#pendingRow { background: @glass@; border: 1px solid @glass_edge@; border-radius: 14px; }
QFrame#pendingRow:hover { background: @glass_hover@; border: 1px solid @primary@; }
QLabel#pendingName { font-weight: 700; }
QLabel#pendingAmount { font-weight: 700; }
QPushButton#rowAction, QPushButton#rowDanger { padding: 3px 10px; min-height: 0; font-size: 12px; border-radius: 8px; }
QPushButton#rowDanger { background: @danger@; color: @primary_text@; border: none; }
QPushButton#rowDanger:hover { background: @danger_gradient@; }

/* ---- buttons: blue gradient main action, glass secondary, solid status ones ----------------------- */
QPushButton { background: @glass_strong@; color: @navy@; border: 1px solid @glass_blue_edge@;
              border-radius: @radius_ctrl@; padding: 9px 18px; min-height: 20px; }
QPushButton:hover { background: @glass_hover@; border: 1px solid @primary@; }
QPushButton:pressed { background: @glass_selection@; }
QPushButton:focus { border: 2px solid @focus@; }
QPushButton:disabled { color: @disabled_text@; background: @glass_alt@; border: 1px solid @glass_edge@; }
QPushButton#primary { background: @primary_gradient@; color: @primary_text@; border: 1px solid @primary@;
                      font-weight: 600; }
QPushButton#primary:hover { background: @primary_gradient_hover@; border: 1px solid @primary_hover@; }
QPushButton#primary:pressed { background: @primary@; }
QPushButton#primary:disabled { background: @glass_alt@; color: @disabled_text@; border: 1px solid @glass_edge@; }
QPushButton#danger { background: @danger@; color: @primary_text@; border: none; font-weight: 600; }
QPushButton#danger:hover { background: @danger_gradient@; }
QPushButton#danger:disabled { background: @glass_alt@; color: @disabled_text@; }
QPushButton#success { background: @success@; color: @primary_text@; border: none; font-weight: 600; }
QPushButton#success:hover { background: @success_gradient@; }
QToolButton { background: @glass_input@; color: @text@; border: 1px solid @glass_blue_edge@;
              border-radius: @radius_ctrl@; padding: 6px 10px; }
QToolButton:hover { background: @glass_hover@; border: 1px solid @primary@; }

/* ---- inputs -------------------------------------------------------------------------------------- */
QLineEdit, QComboBox, QDateEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background: @glass_input@; color: @text@; border: 1px solid @input_border@; border-radius: @radius_ctrl@;
    padding: 8px 10px; min-height: 20px; selection-background-color: @glass_selection@; selection-color: @text@; }
QLineEdit:hover, QComboBox:hover, QDateEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border: 1px solid @muted@; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 2px solid @focus@; background: @glass_hover@; padding: 7px 9px; }
QLineEdit[invalid="true"] { border: 2px solid @danger@; background: @danger_bg@; padding: 7px 9px; }
QLineEdit:read-only { background: @glass_alt@; color: @muted@; }
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: @disabled_text@; background: @glass_alt@; }
QComboBox QAbstractItemView, QCompleter QAbstractItemView { background: @popup@; color: @text@;
                              border: 1px solid @glass_blue_edge@;
                              selection-background-color: @selection@; selection-color: @navy@; outline: 0; }
QMenu { background: @popup@; color: @text@; border: 1px solid @glass_blue_edge@; border-radius: @radius_ctrl@;
        padding: 4px; }
QMenu::item { padding: 8px 20px; border-radius: 8px; }
QMenu::item:selected { background: @selection@; color: @navy@; }
QCalendarWidget QWidget { background: @popup@; color: @text@; }
QCalendarWidget QToolButton { background: transparent; color: @navy@; font-weight: 700; border: none; }
QCalendarWidget QTableView { selection-background-color: @selection@; selection-color: @navy@; }

/* ---- tables: readable rows, strong selection ----------------------------------------------------- */
QTableWidget, QTableView { background: @glass_row@; alternate-background-color: @glass_alt@; color: @text@;
               border: 1px solid @glass_edge@; border-radius: @radius_table@; gridline-color: transparent;
               selection-background-color: @glass_selection@; selection-color: @navy@; }
QTableWidget::item { padding: 5px 10px; border: none; border-bottom: 1px solid @glass_line@; }
QTableWidget::item:hover { background: @glass_row_hover@; }
QTableWidget::item:selected { background: @glass_selection@; color: @navy@; }
QHeaderView { background: transparent; }
QHeaderView::section { background: @glass_header@; color: @navy@; border: none;
                       border-bottom: 2px solid @glass_blue_edge@; padding: 9px 10px; font-weight: 700; font-size: 13px; }
QTableCornerButton::section { background: @glass_header@; border: none; }

/* ---- tabs ---------------------------------------------------------------------------------------- */
QTabWidget::pane { border: 1px solid @glass_edge@; border-radius: @radius_table@; background: @glass@; top: -1px; }
QTabBar::tab { background: transparent; color: @muted@; padding: 10px 18px; border: none;
               border-bottom: 3px solid transparent; font-weight: 600; }
QTabBar::tab:hover { color: @text@; background: @glass_alt@; border-radius: @radius_ctrl@; }
QTabBar::tab:selected { color: @primary@; border-bottom: 3px solid @primary@; background: @glass_strong@;
                        border-radius: @radius_ctrl@; }

/* ---- bars, scrollbars ---------------------------------------------------------------------------- */
QProgressBar { background: @glass_line@; border: none; border-radius: 5px; height: 10px; text-align: center;
               color: transparent; }
QProgressBar::chunk { background: @primary_gradient@; border-radius: 5px; }
QProgressBar[state="warn"]::chunk { background: @warning@; }
QProgressBar[state="over"]::chunk { background: @danger@; }

QScrollBar:vertical { background: transparent; width: 13px; margin: 3px; }
QScrollBar::handle:vertical { background: @glass_line@; border-radius: 5px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: @muted@; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 13px; margin: 3px; }
QScrollBar::handle:horizontal { background: @glass_line@; border-radius: 5px; min-width: 36px; }
QScrollBar::handle:horizontal:hover { background: @muted@; }

/* ---- chrome: status bar, tooltips, dialogs, check boxes ------------------------------------------ */
QStatusBar { background: @glass@; color: @muted@; border-top: 1px solid @glass_edge@; }
QToolTip { background: @glass_strong@; color: @text@; border: 1px solid @glass_blue_edge@; border-radius: 8px;
           padding: 6px 8px; }
QMessageBox, QInputDialog { background: @bg_gradient@; }
QCheckBox, QRadioButton { color: @text@; spacing: 8px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; }
/* Fusion paints an empty box so faintly on the glass cards that an unchecked box disappears; give the empty
   states a border of their own. Checked states are left to the style, which draws the tick. */
QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked { background: @glass_alt@;
    border: 1px solid @muted@; }
QCheckBox::indicator:unchecked { border-radius: 5px; }
QRadioButton::indicator:unchecked { border-radius: 9px; }
QCheckBox::indicator:unchecked:hover, QCheckBox::indicator:unchecked:focus,
QRadioButton::indicator:unchecked:hover, QRadioButton::indicator:unchecked:focus { border: 1px solid @primary@; }
"""


def build_stylesheet(name: str) -> str:
    palette = PALETTES.get(name, PALETTES["light"])
    return re.sub(r"@(\w+)@", lambda m: palette[m.group(1)], _TEMPLATE)
