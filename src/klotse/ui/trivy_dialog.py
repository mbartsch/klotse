"""View > Trivy reports…: the Trivy Operator's reports (see k8s/trivy.py) in four tabs: vulnerabilities, config audit, exposed secrets and RBAC.
Looking only. A tab loads the first time it is shown; double-click opens the full report of that workload. Matched secret text is never shown."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..k8s import trivy
from . import theme

BASE = ["Namespace", "Workload", "Container"]
COUNTS = ["Critical", "High", "Medium", "Low", "Unknown"]


class _Number(QTableWidgetItem):
    """A cell that sorts by its number."""

    def __init__(self, value):
        super().__init__(str(value))
        self.value = value
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        return self.value < getattr(other, "value", 0)


class _Tab(QWidget):
    def __init__(self, key, title):
        super().__init__()
        self.key, self.title, self.rows, self.loaded = key, title, [], False
        self.summary = QLabel("Not loaded")
        self.summary.setWordWrap(True)
        theme.muted(self.summary)
        self.filter = QLineEdit(placeholderText="Filter by namespace, workload, container or image…", clearButtonEnabled=True)
        self.filter.textChanged.connect(lambda _: self.apply_filter())
        columns = BASE + (["Image"] if key in ("vulnerabilities", "secrets") else []) + COUNTS
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.columns = columns
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)

    def show_rows(self, rows):
        self.rows, self.loaded = rows, True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            image = f"{row['repository']}:{row['tag']}" if row["repository"] else ""
            cells = [row["namespace"], f"{row['workload_kind']}/{row['workload']}" if row["workload"] else row["report"], row["container"]]
            if "Image" in self.columns:
                cells.append(image)
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(text))
            for offset, name in enumerate(COUNTS):
                self.table.setItem(r, len(cells) + offset, _Number(row[name.lower()]))
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        # the rows arrive worst first; Qt would otherwise apply its default sort (the first column) when sorting is switched on
        self.table.sortItems(len(self.columns) - len(COUNTS), Qt.DescendingOrder)  # Critical, highest first (a stable sort keeps the order of ties)
        self.summary.setText(trivy.summary_text(rows, self.title))
        self.apply_filter()

    def apply_filter(self):
        words = self.filter.text().lower().split()
        for r in range(self.table.rowCount()):
            line = " ".join(self.table.item(r, c).text() for c in range(len(self.columns) - len(COUNTS))).lower()
            self.table.setRowHidden(r, not all(w in line for w in words))

    def row_at(self, table_row):
        """The report row shown in table row `table_row` (the table may be sorted, so match on the cells, not the position)."""
        name, container = self.table.item(table_row, 1).text(), self.table.item(table_row, 2).text()
        namespace = self.table.item(table_row, 0).text()
        for row in self.rows:
            shown = f"{row['workload_kind']}/{row['workload']}" if row["workload"] else row["report"]
            if (row["namespace"], shown, row["container"]) == (namespace, name, container):
                return row
        return None


class TrivyDialog(QDialog):
    load_requested = Signal(str)  # a tab key: read its reports
    open_requested = Signal(str, dict)  # tab key, report row: read and show the whole report

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trivy reports")
        self.resize(1000, 560)
        self.header = QLabel(f"<b>{context}</b> · Trivy Operator reports")
        self.header.setTextFormat(Qt.RichText)
        self.tabs = QTabWidget()
        self.pages = {}
        for key, (_plural, title, _kind) in trivy.KINDS.items():
            page = self.pages[key] = _Tab(key, title)
            page.table.cellDoubleClicked.connect(lambda r, _c, p=page: self._open(p, r))
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.reload_button = QPushButton("Read again")
        self.reload_button.clicked.connect(lambda: self.load_current(force=True))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel("Reports are written by the Trivy Operator next to each workload; Klotse only reads them. Double-click a row for the "
                      "whole report. Exposed secret reports never show the matched text.")
        note.setWordWrap(True)
        theme.muted(note)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(note)
        lay.addLayout(buttons)

    def current_page(self):
        return self.tabs.currentWidget()

    def load_current(self, force=False):
        page = self.current_page()
        if force or not page.loaded:
            page.summary.setText("Reading…")
            self.load_requested.emit(page.key)

    def _tab_changed(self, _index):
        self.load_current()

    def show_rows(self, key, rows):
        self.pages[key].show_rows(rows)

    def show_missing(self, key):
        page = self.pages[key]
        page.rows, page.loaded = [], True
        page.table.setRowCount(0)
        page.summary.setText("The Trivy Operator's report kinds were not found in this cluster (aquasecurity.github.io), so there is nothing to show. "
                             "Install the operator to get vulnerability, config audit, secret and RBAC reports.")

    def show_error(self, key, message):
        self.pages[key].summary.setText(f"Could not read the reports: {message}")

    def _open(self, page, table_row):
        row = page.row_at(table_row)
        if row is not None:
            self.open_requested.emit(page.key, row)


class ReportDialog(QDialog):
    """One report in full: its findings in a table with a filter and Copy."""

    def __init__(self, title, headings, rows, scanned="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 520)
        self.rows = rows
        self.summary = QLabel(f"{len(rows)} findings" + (f" · scanned {scanned}" if scanned else ""))
        theme.muted(self.summary)
        self.filter = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        self.filter.textChanged.connect(lambda _: self._apply())
        self.table = QTableWidget(len(rows), len(headings))
        self.table.setHorizontalHeaderLabels(headings)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()
        copy = QPushButton("Copy as text")
        copy.clicked.connect(self.copy)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)

    def _apply(self):
        words = self.filter.text().lower().split()
        for r, row in enumerate(self.rows):
            self.table.setRowHidden(r, not all(w in " ".join(map(str, row)).lower() for w in words))

    def copy(self):
        """The visible rows as tab-separated text on the clipboard."""
        lines = ["\t".join(str(c) for c in row) for r, row in enumerate(self.rows) if not self.table.isRowHidden(r)]
        QApplication.clipboard().setText("\n".join(lines))
        return len(lines)
