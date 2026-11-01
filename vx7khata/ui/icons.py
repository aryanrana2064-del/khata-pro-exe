"""Small line icons drawn with QPainter - no icon font, no image files, no extra dependency.

Every glyph is stroked in 24x24 units and scaled to the requested pixel size, so the same shape works for a sidebar
row, a KPI badge or a tool button. The colour comes from the active palette, which is why icons are rebuilt whenever
the theme changes (see :meth:`vx7khata.ui.main_window.MainWindow._apply_theme`).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel

UNIT = 24.0


def _begin(size: int, colour: str) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / UNIT, size / UNIT)
    pen = QPen(QColor(colour))
    pen.setWidthF(1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    return pm, p


def _line(p: QPainter, x1, y1, x2, y2) -> None:
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(*points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def _grid(p):  # dashboard
    for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(QRectF(x, y, 7, 7), 2, 2)


def _list(p):  # daily summary
    for y in (7.0, 12.0, 17.0):
        p.drawEllipse(QPointF(5.0, y), 1.2, 1.2)
        _line(p, 9, y, 20 if y != 12.0 else 16, y)


def _people(p):  # customers
    p.drawEllipse(QPointF(9.5, 8.0), 3.2, 3.2)
    p.drawArc(QRectF(4.3, 12.5, 10.4, 10.4), 0, 180 * 16)
    p.drawEllipse(QPointF(17.0, 9.0), 2.4, 2.4)
    p.drawArc(QRectF(13.1, 13.2, 7.8, 7.8), 0, 180 * 16)


def _pencil(p):  # new entry
    _line(p, 5, 19, 14.5, 9.5)
    p.drawPolyline(_poly((14.5, 9.5), (17.5, 6.5), (20, 9), (17, 12)))
    _line(p, 5, 19, 4.2, 20.2)


def _arrow_out(p):  # debit / udhaar given away
    _line(p, 7, 17, 17, 7)
    _line(p, 17, 7, 11, 7)
    _line(p, 17, 7, 17, 13)


def _arrow_in(p):  # credit / jama received
    _line(p, 17, 7, 7, 17)
    _line(p, 7, 17, 13, 17)
    _line(p, 7, 17, 7, 11)


def _scale(p):  # net outstanding
    _line(p, 12, 4.5, 12, 19)
    _line(p, 5, 7.5, 19, 7.5)
    p.drawArc(QRectF(2.2, 12.5, 5.6, 5.6), 180 * 16, 180 * 16)
    p.drawArc(QRectF(16.2, 12.5, 5.6, 5.6), 180 * 16, 180 * 16)
    _line(p, 5, 7.5, 5, 12.5)
    _line(p, 19, 7.5, 19, 12.5)
    _line(p, 8.5, 19.5, 15.5, 19.5)


def _calendar(p, mark: bool = False):
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 15), 2.5, 2.5)
    _line(p, 3.5, 10, 20.5, 10)
    _line(p, 8, 3.5, 8, 7)
    _line(p, 16, 3.5, 16, 7)
    if mark:
        p.setBrush(QColor(p.pen().color()))
        p.drawEllipse(QPointF(12, 15.5), 1.7, 1.7)
        p.setBrush(Qt.BrushStyle.NoBrush)


def _calendar_plus(p):  # daily entry
    _calendar(p)
    _line(p, 12, 13.2, 12, 18)
    _line(p, 9.6, 15.6, 14.4, 15.6)


def _book(p):  # ledger
    p.drawRoundedRect(QRectF(5, 3.5, 14, 17), 2, 2)
    _line(p, 8.6, 3.5, 8.6, 20.5)
    for y in (9.0, 13.0, 17.0):
        _line(p, 11.5, y, 16.5, y)


def _shield(p):  # recovery
    p.drawPolyline(_poly((12, 3.2), (19.5, 6), (19.5, 12), (12, 20.8), (4.5, 12), (4.5, 6), (12, 3.2)))
    p.drawPolyline(_poly((8.8, 11.6), (11.2, 14.2), (15.6, 9.4)))


def _document(p):  # reports
    p.drawPolyline(_poly((14, 3.5), (6, 3.5), (6, 20.5), (18, 20.5), (18, 7.5), (14, 3.5)))
    p.drawPolyline(_poly((14, 3.5), (14, 7.5), (18, 7.5)))
    for y in (12.0, 15.0, 18.0):
        _line(p, 9, y, 15, y)


def _pulse(p):  # activity
    p.drawPolyline(_poly((3, 13), (7.5, 13), (10, 6.5), (13.5, 18), (16, 13), (21, 13)))


def _gear(p):  # settings
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    p.drawEllipse(QPointF(12, 12), 7.2, 7.2)
    for step in range(8):
        angle = step * 45
        p.save()
        p.translate(12, 12)
        p.rotate(angle)
        _line(p, 0, -7.2, 0, -9.6)
        p.restore()


def _wallet(p):
    p.drawRoundedRect(QRectF(3, 6.5, 18, 12), 2.5, 2.5)
    _line(p, 3, 10.5, 21, 10.5)
    p.drawEllipse(QPointF(16.5, 14.8), 1.3, 1.3)


def _history(p):
    p.drawEllipse(QPointF(12, 12), 8.2, 8.2)
    _line(p, 12, 12, 12, 7.2)
    _line(p, 12, 12, 15.8, 13.6)


def _alert(p):
    p.drawPolyline(_poly((12, 3.6), (21, 19.8), (3, 19.8), (12, 3.6)))
    _line(p, 12, 9.5, 12, 14.5)
    p.drawEllipse(QPointF(12, 17.2), 0.9, 0.9)


def _bars(p):  # chart
    _line(p, 4, 19.5, 20, 19.5)
    for x, top in ((7.5, 13.0), (12, 8.0), (16.5, 11.0)):
        _line(p, x, 19.5, x, top)


def _speaker(p):  # voice assistant
    p.drawPolyline(_poly((4, 10), (8, 10), (12.5, 5.5), (12.5, 18.5), (8, 14), (4, 14), (4, 10)))
    for radius in (3.4, 6.4):
        p.drawArc(QRectF(12.5 - radius, 12 - radius, radius * 2, radius * 2), -52 * 16, 104 * 16)


_SHAPES = {
    "dashboard": _grid,
    "summary": _list,
    "customers": _people,
    "users": _people,
    "entry": _pencil,
    "daily": _calendar_plus,
    "ledger": _book,
    "recovery": _shield,
    "reports": _document,
    "activity": _pulse,
    "settings": _gear,
    "debit": _arrow_out,
    "credit": _arrow_in,
    "net": _scale,
    "today": lambda p: _calendar(p, mark=True),
    "calendar": _calendar,
    "history": _history,
    "wallet": _wallet,
    "alert": _alert,
    "chart": _bars,
    "voice": _speaker,
    "speaker": _speaker,
}


def pixmap(name: str, colour: str, size: int = 22) -> QPixmap:
    """A freshly drawn glyph; unknown names give a blank square rather than an error."""
    pm, painter = _begin(size, colour)
    shape = _SHAPES.get(name)
    if shape is not None:
        shape(painter)
    painter.end()
    return pm


def icon(name: str, colour: str, size: int = 22, checked_colour: str | None = None) -> QIcon:
    """Icon for a button. ``checked_colour`` paints the variant Qt shows while a checkable button is checked
    (a checked QPushButton is drawn sunken/selected, so those two modes carry the variant)."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, colour, size))
    if checked_colour:
        checked = pixmap(name, checked_colour, size)
        ic.addPixmap(checked, QIcon.Mode.Active)
        ic.addPixmap(checked, QIcon.Mode.Selected)
    return ic


def icon_label(name: str, colour: str, size: int = 22, parent=None) -> QLabel:
    """A glass badge holding one glyph (used on the dashboard metric cards)."""
    label = QLabel(parent)
    label.setObjectName("kpiIcon")
    label.setPixmap(pixmap(name, colour, int(size * 0.62)))
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label
