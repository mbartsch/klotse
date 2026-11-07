"""View > Capacity…: what the pods request and may use against what each node can give, per node and per namespace
(like kube-capacity). Numbers come from `k8s.capacity`; percentages are written out, so nothing depends on colour."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..k8s import capacity
from . import theme

NODE_COLUMNS = ["Node", "Pods", "CPU alloc", "CPU req", "CPU req %", "CPU lim", "CPU used", "Mem alloc GiB", "Mem req GiB",
                "Mem req %", "Mem lim GiB", "Mem used GiB"]
SPACE_COLUMNS = ["Namespace", "Pods", "CPU req", "CPU lim", "CPU used", "Mem req GiB", "Mem lim GiB", "Mem used GiB"]


def number(value, digits=2):
    """'1.5'; blank for an unknown (-1) amount, so 0 and unknown stay apart."""
    return "" if value < 0 else f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"


def percent_text(value):
    return "" if value < 0 else f"{value:.0f}%" + (" over" if value > 100 else "")


class _Item(QTableWidgetItem):
    """A cell that sorts by its number, not its text."""

    def __init__(self, text, key):
        super().__init__(text)
        self._key = key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)
        if not isinstance(key, str):
            self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        mine, theirs = self._key, getattr(other, "_key", None)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


def node_cells(r):
    return [(r["name"], r["name"].lower()), (str(r["pods"]), r["pods"]),
            (number(r["cpu_alloc"]), r["cpu_alloc"]), (number(r["cpu_req"]), r["cpu_req"]),
            (percent_text(r["cpu_req_pct"]), r["cpu_req_pct"]), (number(r["cpu_lim"]), r["cpu_lim"]),
            (number(r["cpu_used"]), r["cpu_used"]), (number(r["mem_alloc"]), r["mem_alloc"]),
            (number(r["mem_req"]), r["mem_req"]), (percent_text(r["mem_req_pct"]), r["mem_req_pct"]),
            (number(r["mem_lim"]), r["mem_lim"]), (number(r["mem_used"]), r["mem_used"])]


def space_cells(r):
    return [(r["name"], r["name"].lower()), (str(r["pods"]), r["pods"]), (number(r["cpu_req"]), r["cpu_req"]),
            (number(r["cpu_lim"]), r["cpu_lim"]), (number(r["cpu_used"]), r["cpu_used"]),
            (number(r["mem_req"]), r["mem_req"]), (number(r["mem_lim"]), r["mem_lim"]), (number(r["mem_used"]), r["mem_used"])]


def _table(columns):
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows, cells):
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, (text, key) in enumerate(cells(row)):
            table.setItem(i, j, _Item(text, key))
    table.setSortingEnabled(True)
    table.resizeColumnsToContents()


class CapacityDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capacity")
        self.resize(980, 560)
        self.header = QLabel(f"<b>{_plain(context)}</b>")
        self.header.setTextFormat(Qt.RichText)
        self.summary = QLabel("Loading nodes and pods…")
        self.summary.setWordWrap(True)
        theme.muted(self.summary)
        self.nodes = _table(NODE_COLUMNS)
        self.spaces = _table(SPACE_COLUMNS)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.nodes, "Nodes")
        self.tabs.addTab(self.spaces, "Namespaces")
        self.reload_button = QPushButton("Reload")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.summary)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(buttons)

    def show_data(self, nodes, pods):
        node_rows = capacity.by_node(nodes, pods)
        total = capacity.totals(node_rows)
        _fill(self.nodes, node_rows, node_cells)
        _fill(self.spaces, capacity.by_namespace(pods), space_cells)
        self.summary.setText(
            f"Requested of allocatable: CPU {number(total['cpu_req'])} / {number(total['cpu_alloc'])} cores "
            f"({percent_text(total['cpu_req_pct']) or 'n/a'}), memory {number(total['mem_req'])} / {number(total['mem_alloc'])} GiB "
            f"({percent_text(total['mem_req_pct']) or 'n/a'}). Finished pods are not counted; blank usage means metrics-server "
            "did not answer. Click a heading to sort.")

    def show_error(self, message):
        self.summary.setText(f"Could not load: {message}")


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
