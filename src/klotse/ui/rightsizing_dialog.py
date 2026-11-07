"""View > Right-sizing…: requests and limits against current usage, per workload, with advice (see k8s/rightsizing.py). Looking only.
Usage is one sample taken now, which the dialog says; double-click goes to the workload."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QVBoxLayout)

from ..k8s import rightsizing as rs
from . import theme
from .capacity_dialog import _Item

HEADERS = ["Workload", "Namespace", "Pods", "CPU request", "CPU used (max)", "CPU advice", "CPU verdict", "Mem request",
           "Mem used (max)", "Mem advice", "Mem verdict"]


def cells(r):
    """(text, sort key) per column."""
    return [(f"{r['kind']}/{r['name']}", f"{r['kind']}/{r['name']}".lower()), (r["namespace"], r["namespace"].lower()),
            (str(r["pods"]), r["pods"]),
            (rs.cpu_text(r["cpu_req"]) if r["cpu_req"] > 0 else "none", r["cpu_req"]), (rs.cpu_text(r["cpu_used"]), r["cpu_used"]),
            (rs.cpu_text(r["cpu_suggest"]), r["cpu_suggest"] or -1), (rs.TEXT[r["cpu_verdict"]], r["cpu_verdict"]),
            (rs.mem_text(r["mem_req"]) if r["mem_req"] > 0 else "none", r["mem_req"]), (rs.mem_text(r["mem_used"]), r["mem_used"]),
            (rs.mem_text(r["mem_suggest"]), r["mem_suggest"] or -1), (rs.TEXT[r["mem_verdict"]], r["mem_verdict"])]


class RightSizingDialog(QDialog):
    go_to_requested = Signal(str, str, str)  # kind, namespace, name

    def __init__(self, context, scope, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Right-sizing")
        self.resize(1100, 560)
        self.rows = []
        self.header = QLabel(f"<b>{_plain(context)}</b> · {_plain(scope)}")
        self.header.setTextFormat(Qt.RichText)
        self.summary = QLabel("Loading pods and usage…")
        self.summary.setWordWrap(True)
        theme.muted(self.summary)
        self.only_attention = QCheckBox("Only workloads that need a look")
        self.only_attention.setChecked(True)
        self.only_attention.toggled.connect(lambda _=None: self._apply_filter())
        self.filter = QLineEdit(placeholderText="Filter by name or namespace…", clearButtonEnabled=True)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.reload_button = QPushButton("Reload")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel("Advice keeps 25% headroom above the largest usage among a workload's pods right now (CPU rounded up to 10m, "
                      "memory to 16Mi). A restart-time spike, a nightly job or traffic peak is not in one sample. Nothing is changed here.")
        note.setWordWrap(True)
        theme.muted(note)
        options = QHBoxLayout()
        options.addWidget(self.only_attention)
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

    def show_pods(self, pods):
        self.rows = rs.analyze(pods)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for j, (text, key) in enumerate(cells(row)):
                self.table.setItem(i, j, _Item(text, key))
            self.table.item(i, 0).setData(Qt.UserRole, (row["kind"], row["namespace"], row["name"]))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.summary.setText(rs.summary(self.rows))
        self._apply_filter()

    def show_error(self, message):
        self.summary.setText(f"Could not load: {message}")

    def _apply_filter(self):
        words = self.filter.text().lower().split()
        for i in range(self.table.rowCount()):
            row = self._row_for(i)
            line = f"{row['kind']}/{row['name']} {row['namespace']}".lower()
            hidden = (self.only_attention.isChecked() and not rs.needs_attention(row)) or not all(w in line for w in words)
            self.table.setRowHidden(i, hidden)

    def _row_for(self, table_row):
        """The analysis row behind a table row (the table may be sorted differently from `self.rows`)."""
        kind, namespace, name = self.table.item(table_row, 0).data(Qt.UserRole)
        return next(r for r in self.rows if (r["kind"], r["namespace"], r["name"]) == (kind, namespace, name))

    def _open_row(self, table_row, _column):
        self.go_to_requested.emit(*self.table.item(table_row, 0).data(Qt.UserRole))


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
