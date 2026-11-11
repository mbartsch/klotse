"""Small theme-aware action icons drawn as vector paths for consistent rendering across platforms."""
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

from . import theme


NAMES = frozenset({"search", "refresh", "create", "edit", "delete", "copy", "detach", "attach"})


class _ActionIcon(QIconEngine):
    def __init__(self, name):
        super().__init__()
        if name not in NAMES:
            raise ValueError(f"unknown action icon: {name}")
        self.name = name

    def clone(self):
        return _ActionIcon(self.name)

    def paint(self, painter, rect, mode, state):
        del mode, state
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        pen = QPen(QColor(theme.TEXT), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        if self.name == "search":
            path.addEllipse(QRectF(3.8, 3.8, 11.5, 11.5))
            path.moveTo(12.2, 12.2)
            path.lineTo(20, 20)
        elif self.name == "refresh":
            path.arcMoveTo(QRectF(4, 4, 16, 16), 42)
            path.arcTo(QRectF(4, 4, 16, 16), 42, 286)
            painter.drawPath(path)
            painter.drawLine(QPointF(17.1, 3.8), QPointF(20.6, 4.1))
            painter.drawLine(QPointF(20.6, 4.1), QPointF(20.3, 7.5))
            painter.restore()
            return
        elif self.name == "copy":
            path.addRoundedRect(QRectF(8, 3.5, 12, 14), 1.2, 1.2)
            painter.drawPath(path)
            path = QPainterPath()
            path.addRoundedRect(QRectF(4, 7, 12, 14), 1.2, 1.2)
        elif self.name == "detach":
            path.addRoundedRect(QRectF(3.5, 8, 11, 12), 1.2, 1.2)
            painter.drawPath(path)
            path = QPainterPath()
            path.moveTo(9, 15)
            path.lineTo(19.5, 4.5)
            path.moveTo(14.2, 4.5)
            path.lineTo(19.5, 4.5)
            path.lineTo(19.5, 9.8)
        elif self.name == "attach":
            path.addRoundedRect(QRectF(3.5, 3.5, 17, 17), 1.2, 1.2)
            painter.drawPath(path)
            path = QPainterPath()
            path.moveTo(19, 5)
            path.lineTo(10, 14)
            path.moveTo(10, 9.5)
            path.lineTo(10, 14)
            path.lineTo(14.5, 14)
        elif self.name == "create":
            path.moveTo(12, 4)
            path.lineTo(12, 20)
            path.moveTo(4, 12)
            path.lineTo(20, 12)
        elif self.name == "edit":
            path.moveTo(4, 17.5)
            path.lineTo(3, 21)
            path.lineTo(6.5, 20)
            path.lineTo(19.4, 7.1)
            path.lineTo(16.9, 4.6)
            path.closeSubpath()
            path.moveTo(15.8, 5.7)
            path.lineTo(18.3, 8.2)
        elif self.name == "delete":
            path.moveTo(5, 7)
            path.lineTo(19, 7)
            path.moveTo(9, 7)
            path.lineTo(9, 4)
            path.lineTo(15, 4)
            path.lineTo(15, 7)
            path.moveTo(7, 9)
            path.lineTo(8, 20)
            path.lineTo(16, 20)
            path.lineTo(17, 9)
            path.moveTo(10, 10)
            path.lineTo(10, 17)
            path.moveTo(14, 10)
            path.lineTo(14, 17)
        painter.drawPath(path)
        painter.restore()

    def pixmap(self, size, mode, state):
        image = QPixmap(size)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return image

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(QSize(round(size.width() * scale), round(size.height() * scale)))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap


def action_icon(name):
    """A vector icon that reads the active theme color when Qt paints it."""
    return QIcon(_ActionIcon(name))
