"""The Activity tab of Logs & activity: what the actions on this cluster did, successes and failures alike, with the complete
error text of a failure (#497). Kept in memory for the session; never Secret data or manifests, only the messages shown."""
from datetime import datetime

from PySide6.QtGui import QFontDatabase, QGuiApplication, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

MAX_ENTRIES = 500  # older entries fall off


class Activity:
    """The entries, pure data: (time text, ok, text, detail). The workspace owns one; the panel shows it."""

    def __init__(self, clock=datetime.now):
        self.entries = []
        self._clock = clock
        self.listener = None  # called with each new entry while the tab is open

    def add(self, ok, text, detail=""):
        entry = (self._clock().strftime("%H:%M:%S"), bool(ok), str(text), str(detail or ""))
        self.entries.append(entry)
        del self.entries[:-MAX_ENTRIES]
        if self.listener is not None:
            self.listener(entry)
        return entry


def render(entry):
    stamp, ok, text, detail = entry
    line = f"{stamp}  {'✔' if ok else '✖'}  {text}"
    if detail and detail.strip() != text.strip():
        line += "\n" + "\n".join("            " + part for part in detail.strip().splitlines())
    return line


class ActivityPanel(QWidget):
    key = "activity"

    def __init__(self, activity, parent=None):
        super().__init__(parent)
        self.activity = activity
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setAccessibleName("Activity: results and errors of actions on this cluster")
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)  # a complete error is long: wrap it, do not hide its end
        self.text.setPlaceholderText("Nothing yet. Saves, deletes, restarts, scaling and their errors are listed here.")
        self.copy_button = QPushButton("Copy all")
        self.copy_button.clicked.connect(self._copy)
        note = QLabel("Results and errors of actions on this cluster, this session.")
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.addWidget(note, 1)
        bar.addWidget(self.copy_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.text)
        self.text.setPlainText("\n".join(render(e) for e in activity.entries))
        self.text.moveCursor(QTextCursor.End)
        activity.listener = self._added

    def _added(self, entry):
        self.text.appendPlainText(render(entry))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        self.copy_button.setText("Copied")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1200, self, lambda: self.copy_button.setText("Copy all"))

    def stop(self):
        """Closing the tab: stop listening. The entries stay with the workspace and show again when the tab is reopened."""
        if self.activity.listener == self._added:
            self.activity.listener = None
