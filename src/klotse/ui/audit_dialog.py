"""View > Change log…: the local record of changes made from Klotse (see audit_log.py). Looking only."""
import os

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QVBoxLayout)

from . import analysis_window as aw
from . import theme
from .dialog_actions import action_box
from .preferences import open_folder
from .dialog_sizing import resize_dialog

HEADERS = ["Time (UTC)", "Cluster", "Action", "Target", "Result", "Detail"]
KEYS = ("time", "cluster", "action", "target", "result", "detail")


class AuditDialog(QDialog):
    def __init__(self, log, parent=None):
        super().__init__(parent)
        self.log = log
        self.setWindowTitle("Change log")
        resize_dialog(self, 900, 520)
        self.note = QLabel(f"Changes made from Klotse on this computer, newest first. Stored in {log.path}. Object contents and "
                           "Secret data are never written. Changes made with kubectl in a terminal, or by someone else, are not here.")
        self.note.setWordWrap(True)
        theme.muted(self.note)
        self.filter = QLineEdit(placeholderText="Filter by cluster, action, target or result…", clearButtonEnabled=True)
        self.filter.setAccessibleName("Filter change log")
        self.filter.textChanged.connect(self._filter)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setAccessibleName("Change log entries")
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.found = QLabel("")
        theme.muted(self.found)
        self.reload_button = refresh = QPushButton("Refresh")
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the entries shown as tab-separated text")
        self.copy_button.clicked.connect(lambda: aw.copy_table(self.table))
        folder = QPushButton("Open folder")
        self.clear_button = QPushButton("Clear log…")
        close = QPushButton("Close")
        refresh.clicked.connect(self.load)
        folder.clicked.connect(lambda: open_folder(os.path.dirname(log.path)))
        self.clear_button.clicked.connect(self._clear)
        close.clicked.connect(self.accept)
        buttons = action_box(self, (refresh, QDialogButtonBox.ActionRole),
                             (self.copy_button, QDialogButtonBox.ActionRole),
                             (folder, QDialogButtonBox.ActionRole),
                             (self.clear_button, QDialogButtonBox.DestructiveRole),
                             (close, QDialogButtonBox.RejectRole))
        lay = QVBoxLayout(self)
        lay.addWidget(self.note)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.found)
        lay.addWidget(buttons)
        self.load()

    def load(self):
        entries = self.log.entries()
        aw.fill(self.table, [[("✖ " if key == "result" and entry[key] == "failed" else "") + entry[key] for key in KEYS]
                             for entry in entries], source=False)
        self._filter(self.filter.text())

    def _filter(self, text):
        words = text.lower().split()
        for row in range(self.table.rowCount()):
            line = " ".join((self.table.item(row, c).text() if self.table.item(row, c) else "") for c in range(len(KEYS))).lower()
            self.table.setRowHidden(row, not all(w in line for w in words))
        self.found.setText(aw.found_text(self.table.rowCount(), aw.hidden_count(self.table),
                                         "Nothing found: no change has been made from Klotse on this computer."))

    def _clear(self):
        if QMessageBox.question(self, "Clear change log", "Clear the whole change log on this computer? This cannot be undone.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.log.clear()
        except OSError as e:
            QMessageBox.warning(self, "Change log", str(e))
        self.load()
