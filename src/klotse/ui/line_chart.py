"""A small line chart (QPainter, no extra library): time on the x axis, a value on the y axis, several named lines, optional horizontal
reference lines (a request, a limit). Lines differ by colour, thickness and a legend that also gives each line's latest value, so colour is
never the only cue; reference lines are dashed. Hovering shows the values nearest to the pointer."""
import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from . import theme

PALETTE = ("#2a9d8f", "#e76f51", "#457b9d", "#c99a1c", "#8d6bb8", "#5f8f3e", "#c2332e", "#7f7f7f", "#1d8fa8")
LEFT, RIGHT, TOP, BOTTOM = 66, 16, 30, 26


class LineChart(QWidget):
    hovered_line = Signal(str)
    line_activated = Signal(str)

    def __init__(self, title="", formatter=str, parent=None):
        super().__init__(parent)
        self.title, self.formatter = title, formatter
        self.series, self.refs, self.placeholder = {}, {}, "No samples yet"
        self.selected_line = ""
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(f"{title} usage chart" if title else "Usage chart")
        self.setMouseTracking(True)
        self._update_accessible_description()

    def set_data(self, series, refs=None, placeholder=None):
        """`series`: {name: [(epoch seconds, value)]}; `refs`: {label: value} drawn as dashed horizontal lines."""
        self.series = {name: list(points) for name, points in series.items() if points}
        self.refs = dict(refs or {})
        if self.selected_line not in self.series:
            self.selected_line = next(iter(self.series), "")
        if placeholder is not None:
            self.placeholder = placeholder
        self._update_accessible_description()
        self.update()

    def _update_accessible_description(self):
        if not self.series:
            description = self.placeholder
        else:
            names = ", ".join(self.series)
            points = self.series.get(self.selected_line, ())
            latest = self.formatter(points[-1][1]) if points else "no reading yet"
            activation = ("Enter does not navigate for the total series." if self.selected_line == "total"
                          else "Press Enter to open the selected object's view.")
            description = (f"Selected {self.selected_line}, latest value {latest}. Series: {names}. "
                           f"Use Left and Right to select a series. {activation}")
        self.setAccessibleDescription(description)

    def _select_line(self, index):
        names = list(self.series)
        if not names:
            return
        self.selected_line = names[index % len(names)]
        self._update_accessible_description()
        self.update()

    def keyPressEvent(self, event):
        names = list(self.series)
        if event.key() == Qt.Key_Left and names:
            index = names.index(self.selected_line) if self.selected_line in self.series else 0
            self._select_line(index - 1)
        elif event.key() == Qt.Key_Right and names:
            index = names.index(self.selected_line) if self.selected_line in self.series else -1
            self._select_line(index + 1)
        elif event.key() == Qt.Key_Home and names:
            self._select_line(0)
        elif event.key() == Qt.Key_End and names:
            self._select_line(-1)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.selected_line:
            self.line_activated.emit(self.selected_line)
        else:
            super().keyPressEvent(event)

    # ---- geometry -------------------------------------------------------------------------------------------------
    def plot_rect(self):
        return QRectF(LEFT, TOP, max(10, self.width() - LEFT - RIGHT), max(10, self.height() - TOP - BOTTOM))

    def ranges(self):
        times = [t for points in self.series.values() for t, _ in points]
        values = [v for points in self.series.values() for _, v in points] + list(self.refs.values())
        t0, t1 = (min(times), max(times)) if times else (time.time() - 60, time.time())
        if t1 - t0 < 30:
            t1 = t0 + 30
        top = max(values) if values else 1.0
        return t0, t1, (top * 1.12 if top > 0 else 1.0)

    def _to_point(self, t, value, rect, bounds):
        t0, t1, top = bounds
        return QPointF(rect.left() + (t - t0) / (t1 - t0) * rect.width(), rect.bottom() - value / top * rect.height())

    @staticmethod
    def colour(index):
        return QColor(PALETTE[index % len(PALETTE)])

    # ---- painting -------------------------------------------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        text_colour = self.palette().text().color()
        muted = QColor(text_colour)
        muted.setAlpha(120)
        rect = self.plot_rect()
        painter.setPen(text_colour)
        painter.drawText(QPointF(LEFT, 18), self.title)
        if not self.series:
            painter.setPen(muted)
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)
            self._paint_focus(painter)
            return
        bounds = self.ranges()
        t0, t1, top = bounds
        metrics = QFontMetrics(painter.font())
        for step in range(5):  # grid and y labels
            value = top * step / 4
            y = rect.bottom() - rect.height() * step / 4
            painter.setPen(QPen(muted, 1, Qt.DotLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            label = self.formatter(value)
            painter.setPen(text_colour)
            painter.drawText(QPointF(LEFT - 6 - metrics.horizontalAdvance(label), y + metrics.ascent() / 2 - 1), label)
        for step in range(5):  # x labels, the first one starting at the plot's left edge and the last ending at its right edge
            label = time.strftime("%H:%M:%S", time.localtime(t0 + (t1 - t0) * step / 4))
            width = metrics.horizontalAdvance(label)
            x = rect.left() + rect.width() * step / 4 - width / 2
            painter.drawText(QPointF(max(rect.left(), min(x, rect.right() - width)), self.height() - 8), label)
        for label, value in self.refs.items():  # dashed reference lines
            y = self._to_point(t0, value, rect, bounds).y()
            painter.setPen(QPen(text_colour, 1.4, Qt.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.drawText(QPointF(rect.right() - metrics.horizontalAdvance(f"{label} {self.formatter(value)}") - 2, y - 3), f"{label} {self.formatter(value)}")
        for index, (name, points) in enumerate(self.series.items()):
            width = 3.4 if name == self.selected_line else 2.6 if name == "total" else 1.6
            painter.setPen(QPen(self.colour(index), width))
            if len(points) == 1:  # one sample so far: a dot
                painter.setBrush(self.colour(index))
                painter.drawEllipse(self._to_point(points[0][0], points[0][1], rect, bounds), 3, 3)
            else:
                painter.drawPolyline(QPolygonF([self._to_point(t, v, rect, bounds) for t, v in points]))
        self._paint_legend(painter, metrics)
        self._paint_focus(painter)

    def _paint_focus(self, painter):
        if self.hasFocus():
            painter.save()
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(theme.ACCENT_HI), 2))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 5, 5)
            painter.restore()

    def _paint_legend(self, painter, metrics):
        x = self.width() - RIGHT
        for index, (name, points) in reversed(list(enumerate(self.series.items()))):
            entry = f"{name} {self.formatter(points[-1][1])}"
            width = metrics.horizontalAdvance(entry) + 26
            x -= width
            if x < LEFT + metrics.horizontalAdvance(self.title) + 10:
                break  # out of room: the hover text still names every line
            painter.setPen(QPen(self.colour(index), 2.6 if name == "total" else 1.6))
            painter.drawLine(QPointF(x, 14), QPointF(x + 16, 14))
            painter.setPen(self.palette().text().color())
            painter.drawText(QPointF(x + 20, 18), entry)

    # ---- hover ----------------------------------------------------------------------------------------------------
    def tooltip_at(self, x):
        """The text for pointer position `x` (pixels): the time and, per line, the value of its nearest point."""
        if not self.series:
            return ""
        rect = self.plot_rect()
        t0, t1, _top = self.ranges()
        if not rect.left() <= x <= rect.right():
            return ""
        t = t0 + (x - rect.left()) / rect.width() * (t1 - t0)
        lines = [time.strftime("%H:%M:%S", time.localtime(t))]
        for name, points in self.series.items():
            nearest = min(points, key=lambda p: abs(p[0] - t))
            lines.append(f"{name}: {self.formatter(nearest[1])}")
        return "\n".join(lines)

    def line_at(self, x, y, strict=True):
        """Return the series nearest to the pointer, or an empty string outside the plot."""
        if not self.series:
            return ""
        rect = self.plot_rect()
        if not rect.contains(x, y):
            return ""
        t0, t1, top = self.ranges()
        t = t0 + (x - rect.left()) / rect.width() * (t1 - t0)
        nearest_name, nearest_distance = "", float("inf")
        for name, points in self.series.items():
            point = min(points, key=lambda p: abs(p[0] - t))
            screen_y = rect.bottom() - point[1] / top * rect.height()
            distance = abs(screen_y - y)
            if distance < nearest_distance:
                nearest_name, nearest_distance = name, distance
        return nearest_name if not strict or nearest_distance <= 12 else ""

    def mouseMoveEvent(self, event):
        self.hovered_line.emit(self.line_at(event.position().x(), event.position().y()))
        text = self.tooltip_at(event.position().x())
        if text:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def mouseDoubleClickEvent(self, event):
        # A chart is an interaction surface, not a pixel-perfect hit target: choose the
        # nearest series even when the double-click lands between two painted strokes.
        name = self.line_at(event.position().x(), event.position().y(), strict=False)
        if name:
            self.line_activated.emit(name)
        super().mouseDoubleClickEvent(event)
