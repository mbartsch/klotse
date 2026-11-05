"""Pieces shared by the pod and node action bars: a label that shortens instead of being covered, and one Actions menu
instead of a row of buttons (the row grew wider than the tab and hid the title)."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QFontMetrics
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy, QToolButton


class ElidedLabel(QLabel):
    """A one-line label that cuts its text with … when there is not enough room (the whole text stays in `text()` and in the
    tooltip), and never asks for more width than it is given."""

    def __init__(self, text=""):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(24)
        self.setText(text)

    def setText(self, text):
        self._full = text
        self.setToolTip(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        return QSize(min(metrics.horizontalAdvance(self._full) + 4, 420), metrics.height() + 4)

    def minimumSizeHint(self):
        return QSize(24, QFontMetrics(self.font()).height() + 4)

    def text(self):
        return self._full

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, self.width())
        painter.drawText(self.rect(), int(Qt.AlignVCenter | Qt.AlignLeft), shown)


def actions_button(entries, emit, text="Actions"):
    """A button that opens a menu of `entries`, a list of (label, action name) pairs or None for a separator. Choosing one calls
    emit(action name). Returns (button, [QAction in menu order, separators left out]) so callers can enable or rename them."""
    button = QToolButton()
    button.setText(text)
    button.setToolTip("Everything you can do with this object")
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    menu = QMenu(button)
    actions = []
    for entry in entries:
        if entry is None:
            menu.addSeparator()
            continue
        label, name = entry
        action = QAction(label, menu)
        action.setData(name)
        action.triggered.connect(lambda _checked=False, n=name: emit(n))
        menu.addAction(action)
        actions.append(action)
    button.setMenu(menu)
    return button, actions
