"""View > Search objects…: find objects by name, label, kind or namespace across the common kinds of the cluster in front, without
loading the lists. The query words are described in `k8s/search.py`. The dialog stays open (it is not modal) so several hits can be
visited; double-click or Enter on a hit takes the workspace there. Results are bounded (`search.HIT_LIMIT`)."""
import threading

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from . import theme
from ..k8s.resources import parse_timestamp
from ..k8s.util import format_age

HELP = ("Words must all appear in the name. Also: key=value (a label), kind:pod (or deploy, svc, pvc, node, ...), ns:shop. "
        "Example:  api kind:deploy ns:shop   or   app.kubernetes.io/name=web")
HEADERS = ["Kind", "Namespace", "Name", "Age", "Labels"]


class SearchDialog(QDialog):
    """`search_requested(text, namespace, cancel)` asks the workspace to run the search on a worker (`cancel` is a
    threading.Event set when a newer search replaces this one); the answer comes back through `show_results(serial, hits, notes)`
    or `show_error(serial, message)`; an answer for an older search is ignored."""
    search_requested = Signal(str, str, object)
    goto_requested = Signal(str, str, str)  # kind, namespace, name

    def __init__(self, context, namespace="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Search objects: {context}")
        self.resize(820, 480)
        self.namespace = namespace  # the namespace in front ("" = all namespaces)
        self._serial = 0
        self._cancel = None
        self.hits = []
        self.box = QLineEdit(placeholderText="name words, key=value, kind:pod, ns:shop  (Enter searches)")
        self.box.setClearButtonEnabled(True)
        self.only_ns = QCheckBox(f"Only namespace {namespace}")
        self.only_ns.setVisible(bool(namespace))
        self.only_ns.toggled.connect(self.search_now)
        hint = QLabel(HELP)
        hint.setWordWrap(True)
        theme.muted(hint)
        self.status = QLabel("Type a query. Only names and labels are read from the cluster; no Secret data.")
        self.status.setWordWrap(True)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemDoubleClicked.connect(lambda item: self._go(item.row()))
        self.table.keyPressEvent = self._table_key
        self.go_button = QPushButton("Go to")
        self.go_button.clicked.connect(lambda: self._go(self.table.currentRow()))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.only_ns)
        buttons.addStretch(1)
        buttons.addWidget(self.go_button)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.box)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self._timer = QTimer(self)  # typing pauses 500 ms, then searches; Enter searches at once
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.search_now)
        self.box.textChanged.connect(lambda _t: self._timer.start())
        self.box.returnPressed.connect(self.search_now)

    def _table_key(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._go(self.table.currentRow())
        else:
            QTableWidget.keyPressEvent(self.table, event)

    def search_now(self, *_):
        self._timer.stop()
        if self._cancel is not None:
            self._cancel.set()  # the older search stops reading pages
        self._serial += 1
        text = self.box.text().strip()
        if not text:
            self._cancel = None
            self._fill([], [])
            self.status.setText("Type a query.")
            return
        self._cancel = threading.Event()
        self.status.setText("Searching…")
        self.search_requested.emit(text, self.namespace if self.only_ns.isChecked() else "", self._cancel)

    @property
    def serial(self):
        return self._serial

    def show_results(self, serial, hits, notes):
        if serial != self._serial:
            return
        self._fill(hits, notes)

    def show_error(self, serial, message):
        if serial == self._serial:
            self.status.setText(f"Search failed: {message}")

    def _fill(self, hits, notes):
        self.hits = list(hits)
        self.table.setRowCount(len(self.hits))
        for r, h in enumerate(self.hits):
            values = [h.kind, h.namespace or "—", h.name, format_age(parse_timestamp(h.created)), h.label_text()]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        if self.hits:
            self.table.selectRow(0)
        count = f"{len(self.hits)} match{'' if len(self.hits) == 1 else 'es'}" if (self.hits or not notes) else "No matches"
        self.status.setText(" ".join([count + "."] + notes))
        self.go_button.setEnabled(bool(self.hits))

    def _go(self, row):
        if 0 <= row < len(self.hits):
            h = self.hits[row]
            self.goto_requested.emit(h.kind, h.namespace, h.name)

    def closeEvent(self, event):
        if self._cancel is not None:
            self._cancel.set()
        super().closeEvent(event)
