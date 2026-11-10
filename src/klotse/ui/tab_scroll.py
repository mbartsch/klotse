"""Tab bar that switches tabs when the mouse wheel is used over it."""
from PySide6.QtWidgets import QTabBar


class ScrollableTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_remainder = 0

    def wheelEvent(self, event):
        angle = event.angleDelta().y() or event.angleDelta().x()
        delta = angle or event.pixelDelta().y() or event.pixelDelta().x()
        if not delta or self.count() < 2:
            self._wheel_remainder = 0
            event.ignore()
            return
        # Require two wheel detents per tab change; trackpad pixel deltas use a
        # smaller threshold. Consume at most one tab per event, even for a large delta.
        threshold = 240 if angle else 120
        self._wheel_remainder += delta
        if abs(self._wheel_remainder) < threshold:
            event.accept()
            return
        direction = -1 if self._wheel_remainder > 0 else 1
        self._wheel_remainder -= (1 if self._wheel_remainder > 0 else -1) * threshold
        target = max(0, min(self.count() - 1, self.currentIndex() + direction))
        if target == self.currentIndex():
            self._wheel_remainder = 0
            event.ignore()
            return
        self.setCurrentIndex(target)
        event.accept()
