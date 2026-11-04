from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from .theme import MUTED


class LoadingPane(QWidget):
    """A large, centered "still loading" message with a busy bar, shown in place of a list that has nothing to show
    yet, so a slow first load does not look like a dead page. It blocks nothing."""

    def __init__(self):
        super().__init__()
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"color: {MUTED}; font-size: 22px; font-weight: 600;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # busy / indeterminate
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(260, 8)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(self.label)
        layout.addWidget(self.bar, 0, Qt.AlignHCenter)
        layout.addStretch(2)

    def show_busy(self, text):
        self.label.setText(text)
        self.bar.show()

    def show_failed(self, text):
        self.label.setText(text)
        self.bar.hide()
