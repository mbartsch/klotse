"""View > Health check…: problems and risks found in the manifests of the namespace in front (or all namespaces), like k9s's Popeye.
Looking only. Double-click a finding to go to the object."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..k8s import health
from . import theme

HEADERS = ["Severity", "Kind", "Namespace", "Name", "Problem", "Hint"]


class HealthDialog(QDialog):
    go_to_requested = Signal(str, str, str)  # kind, namespace, name

    def __init__(self, context, scope, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Health check")
        self.resize(1000, 560)
        self.findings = []
        self.header = QLabel(f"<b>{_plain(context)}</b> · {_plain(scope)}")
        self.header.setTextFormat(Qt.RichText)
        self.summary = QLabel("Checking…")
        self.summary.setWordWrap(True)
        theme.muted(self.summary)
        self.show_errors = QCheckBox(health.SYMBOLS[health.ERROR])
        self.show_warnings = QCheckBox(health.SYMBOLS[health.WARN])
        self.show_notes = QCheckBox(health.SYMBOLS[health.INFO])
        for box in (self.show_errors, self.show_warnings, self.show_notes):
            box.setChecked(True)
            box.toggled.connect(lambda _=None: self._apply_filter())
        self.filter = QLineEdit(placeholderText="Filter by kind, namespace, name or problem…", clearButtonEnabled=True)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.reload_button = QPushButton("Check again")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel("A finding is advice, not a verdict: a job may rightly have no probe and one replica may be on purpose. "
                      "Only manifests are read; nothing is changed.")
        note.setWordWrap(True)
        theme.muted(note)
        options = QHBoxLayout()
        for w in (self.show_errors, self.show_warnings, self.show_notes):
            options.addWidget(w)
        options.addWidget(self.filter, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.summary)
        lay.addLayout(options)
        lay.addWidget(self.table, 1)
        lay.addWidget(note)
        lay.addLayout(buttons)

    def show_findings(self, findings, problems=()):
        self.findings = list(findings)
        self.table.setRowCount(len(self.findings))
        for row, f in enumerate(self.findings):
            for col, text in enumerate((health.SYMBOLS[f["severity"]], f["kind"], f["namespace"], f["name"], f["problem"], f["hint"])):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        text = health.summary(self.findings)[1] + "."
        if problems:
            text += " Could not read: " + "; ".join(problems) + "."
        self.summary.setText(text)
        self._apply_filter()

    def show_error(self, message):
        self.summary.setText(f"Could not check: {message}")

    def _apply_filter(self):
        allowed = {health.ERROR: self.show_errors.isChecked(), health.WARN: self.show_warnings.isChecked(),
                   health.INFO: self.show_notes.isChecked()}
        words = self.filter.text().lower().split()
        for row, f in enumerate(self.findings):
            line = " ".join((f["kind"], f["namespace"], f["name"], f["problem"], f["hint"])).lower()
            self.table.setRowHidden(row, not allowed[f["severity"]] or not all(w in line for w in words))

    def _open_row(self, row, _column):
        f = self.findings[row]
        self.go_to_requested.emit(f["kind"], f["namespace"], f["name"])


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
