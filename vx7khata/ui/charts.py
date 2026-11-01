"""Custom-painted monthly credit-given / payment-received bar chart (no extra dependencies)."""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .. import money
from ..labels import JAMA_LABEL, UDHAAR_LABEL
from ..insights import MonthTotals
from ..viewmodel import compact_inr, month_label, nice_axis
from . import theme


def _bar_gradient(colour: str, top: float, bottom: float) -> QLinearGradient:
    """A bar that fades slightly towards the axis - the one decoration the chart allows itself."""
    gradient = QLinearGradient(0, top, 0, bottom)
    strong = QColor(colour)
    soft = QColor(colour)
    soft.setAlphaF(0.72)
    gradient.setColorAt(0.0, strong)
    gradient.setColorAt(1.0, soft)
    return gradient


class MonthlyChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[MonthTotals] = []
        self._hover = -1
        self._plot = QRectF()
        self.setMinimumHeight(250)
        self.setMouseTracking(True)

    def set_data(self, data: Sequence[MonthTotals]) -> None:
        self._data = list(data)
        self._hover = -1
        self.update()

    # ---- painting -----------------------------------------------------
    def paintEvent(self, event) -> None:
        pal = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        left, right, top, bottom = 50.0, 12.0, 30.0, 26.0
        self._plot = QRectF(left, top, max(10.0, w - left - right), max(10.0, h - top - bottom))
        plot = self._plot

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
        p.setFont(small)

        # legend
        lx = left
        for text, key in ((UDHAAR_LABEL, "bar_udhaar"), (JAMA_LABEL, "bar_jama")):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(pal[key])))
            p.drawRoundedRect(QRectF(lx, 8, 10, 10), 3, 3)
            p.setPen(QColor(pal["chart_axis"]))
            p.drawText(QRectF(lx + 14, 4, 60, 18), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            lx += 74

        peak = max([0] + [max(m.udhaar, m.jama) for m in self._data])
        ticks = nice_axis(peak)
        axis_max = ticks[-1] or 1

        # grid + y labels
        for value in ticks:
            y = plot.bottom() - plot.height() * value / axis_max
            p.setPen(QPen(QColor(pal["chart_grid"]), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(pal["chart_axis"]))
            p.drawText(QRectF(0, y - 9, left - 8, 18), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                       compact_inr(value))

        if not self._data or peak == 0:
            p.setPen(QColor(pal["chart_axis"]))
            p.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), "No transactions in this period")
            p.end()
            return

        n = len(self._data)
        group = plot.width() / n
        bar_w = min(group * 0.32, 22.0)
        gap = 3.0
        for i, m in enumerate(self._data):
            cx = plot.left() + group * (i + 0.5)
            if i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                hover_bg = QColor(pal["hover"])
                p.setBrush(QBrush(hover_bg))
                p.drawRoundedRect(QRectF(cx - group / 2 + 2, plot.top(), group - 4, plot.height()), 6, 6)
            for value, key, x in ((m.udhaar, "bar_udhaar", cx - bar_w - gap / 2), (m.jama, "bar_jama", cx + gap / 2)):
                if value <= 0:
                    continue
                bar_h = max(2.0, plot.height() * value / axis_max)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(_bar_gradient(pal[key], plot.bottom() - bar_h, plot.bottom())))
                p.drawRoundedRect(QRectF(x, plot.bottom() - bar_h, bar_w, bar_h), 3, 3)
            p.setPen(QColor(pal["text"] if i == self._hover else pal["chart_axis"]))
            p.drawText(QRectF(cx - group / 2, plot.bottom() + 4, group, 18),
                       int(Qt.AlignmentFlag.AlignCenter), month_label(m.month))
        p.end()

    # ---- hover tooltip ------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        index = -1
        pos = event.position()
        if self._data and self._plot.contains(pos):
            group = self._plot.width() / len(self._data)
            index = min(len(self._data) - 1, max(0, int((pos.x() - self._plot.left()) / group)))
        if index != self._hover:
            self._hover = index
            self.update()
        if index >= 0:
            m = self._data[index]
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{month_label(m.month)}\n{UDHAAR_LABEL}: {money.format_inr(m.udhaar)}\n{JAMA_LABEL}: {money.format_inr(m.jama)}",
                self,
            )
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)
