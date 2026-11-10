"""Tab bar that switches tabs when the mouse wheel is used over it."""
from PySide6.QtWidgets import QTabBar


class ScrollableTabBar(QTabBar):
    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta or self.count() < 2:
            event.ignore()
            return
        steps = max(1, abs(delta) // 120)
        direction = -1 if delta > 0 else 1
        target = max(0, min(self.count() - 1, self.currentIndex() + direction * steps))
        if target == self.currentIndex():
            event.ignore()
            return
        self.setCurrentIndex(target)
        event.accept()
