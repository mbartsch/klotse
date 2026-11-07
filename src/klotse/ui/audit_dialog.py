"""View > Change log…: the local record of changes made from Klotse (see audit_log.py). Looking only."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from . import theme
from .preferences import open_folder

HEADERS = ["Time (UTC)", "Cluster", "Action", "Target", "Result", "Detail"]
KEYS = ("time", "cluster", "action", "target", "result", "detail")


class AuditDialog(QDialog):
    def __init__(self, log, parent=None):
        super().__init__(parent)
        self.log = log
        self.setWindowTitle("Change log")
        self.resize(900, 520)
        self.note = QLabel(f"Changes made from Klotse on this computer, newest first. Stored in {log.path}. Object contents and "
                           "Secret data are never written. Changes made with kubectl in a terminal, or by someone else, are not here.")
        self.note.setWordWrap(True)
        theme.muted(self.note)
        self.filter = QLineEdit(placeholderText="Filter by cluster, action, target or result…", clearButtonEnabled=True)
        self.filter.textChanged.connect(self._filter)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        refresh = QPushButton("Refresh")
        folder = QPushButton("Open folder")
        self.clear_button = QPushButton("Clear log…")
        close = QPushButton("Close")
        refresh.clicked.connect(self.load)
        folder.clicked.connect(lambda: open_folder(os.path.dirname(log.path)))
        self.clear_button.clicked.connect(self._clear)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        for b in (refresh, folder, self.clear_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.note)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)
        self.load()

    def load(self):
        entries = self.log.entries()
        self.table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            for j, key in enumerate(KEYS):
                item = QTableWidgetItem(("✖ " if key == "result" and entry[key] == "failed" else "") + entry[key])
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self._filter(self.filter.text())

    def _filter(self, text):
        words = text.lower().split()
        for row in range(self.table.rowCount()):
            line = " ".join((self.table.item(row, c).text() if self.table.item(row, c) else "") for c in range(len(KEYS))).lower()
            self.table.setRowHidden(row, not all(w in line for w in words))

    def _clear(self):
        if QMessageBox.question(self, "Clear change log", "Delete the whole change log on this computer? This cannot be undone.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.log.clear()
        except OSError as e:
            QMessageBox.warning(self, "Change log", str(e))
        self.load()
