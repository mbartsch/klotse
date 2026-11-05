from time import monotonic

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import theme

MINUTE_HAND_TURN_MS = 8000  # the minute hand goes round once in this time; the hour hand takes 12 times as long
MAX_STEP_DEG = 15.0  # per frame: after a stall (a busy GUI thread) the hands move on a little, they never leap


def hand_angles(turned):
    """(hour hand, minute hand) in degrees clockwise from 12 o'clock, given the minute hand's total turning."""
    return (turned / 12.0) % 360.0, turned % 360.0


class ClockSpinner(QWidget):
    """An old analog clock whose hands keep moving while something loads. It only animates while it is on screen.
    The clock remembers how far it has turned and adds a small step each frame, so hiding, showing, re-laying out or a
    GUI thread starved by loading work (frames then come late and unevenly) never sends the hands back to 12 or makes
    them leap: they carry on from where they were."""

    SIZE = 96

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self._turned = 0.0  # total degrees the minute hand has turned; kept for the life of the widget
        self._last = None  # time of the previous frame (ms), None right after the clock appeared
        self._timer = QTimer(self, interval=40)
        self._timer.timeout.connect(self._tick)

    def angles(self):
        return hand_angles(self._turned)

    def advance(self, now_ms):
        """Turn the hands for the time since the previous frame, at most MAX_STEP_DEG."""
        if self._last is not None:
            step = (now_ms - self._last) / MINUTE_HAND_TURN_MS * 360.0
            self._turned += min(max(step, 0.0), MAX_STEP_DEG)
        self._last = now_ms

    def _tick(self):
        self.advance(monotonic() * 1000.0)
        self.update()

    def showEvent(self, event):
        self._last = None  # time spent hidden does not count; the hands resume where they stopped
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
        face = QColor(theme.MUTED)
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
            p.setPen(QPen(QColor(theme.ACCENT), width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(0, 4), QPointF(0, -length))
            p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
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
        theme.muted(self.label)
        self.label.setStyleSheet("font-size: 22px; font-weight: 600;")
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
