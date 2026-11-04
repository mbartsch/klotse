from time import monotonic

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import ACCENT, MUTED

MINUTE_HAND_TURN_MS = 2000  # the minute hand goes round once in this time; the hour hand takes 12 times as long


def hand_angles(elapsed_ms):
    """(hour hand, minute hand) in degrees clockwise from 12 o'clock at `elapsed_ms` on a clock that never stops."""
    minute = (elapsed_ms / MINUTE_HAND_TURN_MS * 360.0) % 360.0
    return (minute / 12.0) % 360.0, minute


class ClockSpinner(QWidget):
    """An old analog clock whose hands keep moving while something loads. It only animates while it is on screen.
    The hands show the time on one continuous clock (never restarted), so hiding, showing or re-laying out the widget
    cannot send them back to 12: they simply carry on from where they would be."""

    SIZE = 96

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self._timer = QTimer(self, interval=40)
        self._timer.timeout.connect(self.update)

    def angles(self):
        return hand_angles(monotonic() * 1000.0)

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        hour, minute = self.angles()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self.width() / 100.0, self.height() / 100.0)  # draw on a 100 x 100 face, centered on 0,0
        face = QColor(MUTED)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(face, 4))
        p.drawEllipse(QPointF(0, 0), 46, 46)
        p.setPen(QPen(face, 2, Qt.SolidLine, Qt.RoundCap))
        for hour_mark in range(12):  # twelve ticks, longer at 12, 3, 6 and 9
            p.save()
            p.rotate(hour_mark * 30)
            p.drawLine(QPointF(0, -40), QPointF(0, -34 if hour_mark % 3 == 0 else -37))
            p.restore()
        for angle, length, width in ((hour, 24, 5), (minute, 34, 3)):
            p.save()
            p.rotate(angle)
            p.setPen(QPen(QColor(ACCENT), width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(0, 4), QPointF(0, -length))
            p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(0, 0), 4, 4)


class LoadingPane(QWidget):
    """A large, centered "still loading" message with an analog clock, shown in place of a list that has nothing to
    show yet, so a slow first load does not look like a dead page. It blocks nothing."""

    def __init__(self):
        super().__init__()
        self.clock = ClockSpinner()
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"color: {MUTED}; font-size: 22px; font-weight: 600;")
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.clock, 0, Qt.AlignHCenter)
        layout.addSpacing(8)
        layout.addWidget(self.label)
        layout.addStretch(2)

    def show_busy(self, text):
        self.label.setText(text)
        if self.clock.isHidden():  # not on every tick: a needless show() could restart things
            self.clock.show()

    def show_failed(self, text):
        self.label.setText(text)
        self.clock.hide()
