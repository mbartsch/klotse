"""View > Several clusters…: one list of the same kind (Pods, Deployments, Nodes, ...) from several open clusters, with a Cluster column.

The table is the ordinary list table, so sorting and the filter box work, filter expressions included (`Cluster = prod* and Restarts > 3`).
Each cluster is read on its own worker with the loader its own list uses; one cluster failing leaves the others on screen and is named in
the status line. It reads on request (Read again), never by itself. Workload logs and confirmed restarts route to each object's cluster. Double-click goes to the object in its cluster.
The window knows nothing about clusters itself: a `source` (`MainWindow`'s adapter) lists the open clusters and kinds and hands out loaders."""
from PySide6.QtCore import QThreadPool, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

from ..workers import Worker
from . import theme
from .compare_dialog import CompareDialog
from .customizations import DEFAULT_HIDDEN
from .resource_table import LOG_SOURCES, RESTARTABLE, ResourceTable

CLUSTER_COLUMN = ("Cluster", "cluster")


class MultiClusterDialog(QDialog):
    go_to_requested = Signal(str, str, str, str)  # cluster, kind, namespace, name
    action_requested = Signal(str, str, list)  # kind, action, selected rows carrying their cluster

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("Multi Cluster")
        self.resize(1100, 600)
        self._serial = 0
        self._workers = set()
        self._pending, self._failed, self._rows = set(), {}, []
        self.kind_box = QComboBox()
        self.kind_box.setEditable(True)
        self.kind_box.setInsertPolicy(QComboBox.NoInsert)
        self.kind_box.lineEdit().setPlaceholderText("Search kinds…")
        self.kind_box.lineEdit().setClearButtonEnabled(True)
        self._kind_items = []
        self._set_kind_items(source.kinds())
        self.kind_box.lineEdit().textEdited.connect(self._filter_kinds)
        self._kind_refresh = QTimer(self, interval=750)
        self._kind_refresh.timeout.connect(self._refresh_kinds)
        self._kind_refresh.start()
        self.namespace = QLineEdit(placeholderText="All namespaces")
        self.namespace.setMaximumWidth(220)
        self.boxes = {}
        picks = QHBoxLayout()
        for context in source.contexts():
            box = self.boxes[context] = QCheckBox(context)
            box.setChecked(True)
            picks.addWidget(box)
        picks.addStretch(1)
        self.read_button = QPushButton("Read")
        self.read_button.setToolTip("Read the chosen kind from the chosen clusters (nothing reloads by itself)")
        self.read_button.clicked.connect(self.read)
        top = QHBoxLayout()
        top.addWidget(QLabel("Kind"))
        top.addWidget(self.kind_box)
        top.addWidget(QLabel("Namespace"))
        top.addWidget(self.namespace)
        top.addWidget(self.read_button)
        top.addStretch(1)
        self.status = QLabel("Choose a kind and the clusters, then Read.")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.scope = QLabel()
        self.scope.setWordWrap(True)
        self.scope.setTextInteractionFlags(Qt.TextSelectableByMouse)
        theme.muted(self.scope)
        self.table = None
        self.holder = QVBoxLayout()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel("Lists load on request. Workload logs open in each cluster; restarts need confirmation per cluster. "
                      "Each cluster is read with the same loader its own list uses; a cluster that cannot be read is named above "
                      "and the others stay. A very large cluster gives the first page of its list (big-cluster mode). Double-click goes to the "
                      "object in its cluster.")
        note.setWordWrap(True)
        theme.muted(note)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(picks)
        lay.addWidget(self.scope)
        lay.addWidget(self.status)
        lay.addLayout(self.holder, 1)
        lay.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay.addLayout(buttons)
        self.kind_box.currentIndexChanged.connect(lambda _=None: self._new_table())
        for box in self.boxes.values():
            box.toggled.connect(self._update_scope)
        self.namespace.textChanged.connect(self._update_scope)
        self._update_scope()
        self._new_table()

    def _update_scope(self, *_):
        """Keep the chosen cluster set and namespace visible beside the combined results."""
        clusters = self.chosen()
        label = ", ".join(clusters) if clusters else "no clusters selected"
        namespace = self.namespace.text().strip() or "all namespaces"
        self.scope.setText(f"Scope: {label} · namespace: {namespace}")

    def _filter_kinds(self, text):
        """Keep the editable selector useful as a searchable dropdown without losing its chosen item."""
        needle = text.casefold().strip()
        for index in range(self.kind_box.count()):
            title = self.kind_box.itemText(index)
            self.kind_box.view().setRowHidden(index, bool(needle and needle not in title.casefold()))

    def _set_kind_items(self, items):
        self._kind_items = list(items)
        for key, title in self._kind_items:
            self.kind_box.addItem(title, key)

    def _refresh_kinds(self):
        """Pick up CRD-backed views registered after the dialog opened, without disturbing active typing."""
        items = self.source.kinds()
        if items == self._kind_items:
            return
        selected_key = self.kind_key()
        text = self.kind_box.lineEdit().text()
        self.kind_box.blockSignals(True)
        self.kind_box.clear()
        self._set_kind_items(items)
        index = self.kind_box.findData(selected_key)
        if index >= 0:
            self.kind_box.setCurrentIndex(index)
        self.kind_box.setEditText(text)
        self.kind_box.blockSignals(False)
        self._filter_kinds(text)

    # ---- the table for the chosen kind ------------------------------------------------------------------------------
    def kind_key(self):
        return self.kind_box.currentData()

    def _new_table(self):
        """A fresh list for the chosen kind: the Cluster column, then the columns that kind's own list shows."""
        if self.table is not None:
            self.holder.removeWidget(self.table)
            self.table.deleteLater()
        key = self.kind_key()
        kind = self.source.table_kind(key)
        hidden = set(DEFAULT_HIDDEN.get(kind, []))
        columns = [CLUSTER_COLUMN] + [c for c in self.source.columns(key) if c[1] not in hidden]
        self.table = ResourceTable(columns, kind, editable=False, customizable=False)
        if key == "crds":
            self.table.filter.setPlaceholderText("Search CustomResourceDefinitions…")
        self.table.context_actions = self._context_actions
        self.table.view.doubleClicked.connect(lambda _index: self._open_selected())
        self.holder.addWidget(self.table, 1)
        self._rows, self._failed, self._pending = [], {}, set()
        self._serial += 1  # answers still coming for the previous kind are dropped
        self.read_button.setEnabled(True)
        self.status.setText("Choose a kind and the clusters, then Read.")

    def _context_actions(self, menu, rows):
        # Workspace actions need a cluster-specific service; only offer what this view handles.
        if len(rows) == 1:
            menu.addAction("Show in cluster", self._open_selected)
        elif len(rows) == 2 and all(getattr(row, "raw", None) for row in rows):
            menu.addAction("Compare these two…", lambda: self._compare(rows))
        kind = self.source.table_kind(self.kind_key())
        if kind in LOG_SOURCES:
            if not menu.actions()[-1].isSeparator():
                menu.addSeparator()
            menu.addAction("Logs of all pods…", lambda: self.action_requested.emit(kind, "logs", list(rows)))
        if kind in RESTARTABLE:
            if not menu.actions()[-1].isSeparator():
                menu.addSeparator()
            menu.addAction("Restart selected workloads…", lambda: self.action_requested.emit(kind, "restart", list(rows)))

    def _compare(self, rows):
        context = " / ".join(dict.fromkeys(row.cluster for row in rows))
        dialog = CompareDialog(context, self.source.table_kind(self.kind_key()), rows[0], rows[1], self)
        dialog.exec()
        dialog.deleteLater()

    def _open_selected(self):
        rows = self.table.selected_rows()
        if rows:
            row = rows[0]
            self.go_to_requested.emit(row.cluster, self.source.table_kind(self.kind_key()), getattr(row, "namespace", ""), row.name)

    # ---- reading ------------------------------------------------------------------------------------------------------
    def chosen(self):
        return [context for context, box in self.boxes.items() if box.isChecked()]

    def read(self):
        """Start one worker per cluster and render the combined rows when all have answered."""
        contexts = self.chosen()
        if not contexts:
            self.status.setText("Tick at least one cluster.")
            return
        self._serial += 1
        serial = self._serial
        self._rows, self._failed = [], {}
        self._pending = set(contexts)
        self.table.set_rows([])
        namespace = self.namespace.text().strip() or None
        key = self.kind_key()
        self.read_button.setEnabled(False)
        self._status()
        for context in contexts:
            fetch = self.source.fetch(context, key)
            worker = Worker(fetch, namespace)
            worker.setAutoDelete(False)  # keep its signals alive until the queued answer arrives
            self._workers.add(worker)
            worker.signals.result.connect(lambda rows, c=context, w=worker, s=serial: self._arrived(s, c, w, rows, None))
            worker.signals.error.connect(lambda message, c=context, w=worker, s=serial: self._arrived(s, c, w, None, message))
            QThreadPool.globalInstance().start(worker)

    def _arrived(self, serial, context, worker, rows, error):
        self._workers.discard(worker)
        if serial != self._serial:
            return  # an older read, or another kind
        self._pending.discard(context)
        if error is not None:
            self._failed[context] = error.splitlines()[0] if error else "failed"
        else:
            rows = list(rows) if isinstance(rows, (list, tuple)) else []
            for row in rows:
                row.cluster = context
            self._rows += rows
        if not self._pending:
            self.table.set_rows(self._rows)
            self.read_button.setEnabled(True)
        self._status()

    def _status(self):
        done = len(self.chosen()) - len(self._pending)
        text = f"{len(self._rows):,} rows from {done - len(self._failed)} of {len(self.chosen())} clusters"
        if self._pending:
            text += f" · reading {', '.join(sorted(self._pending))}…"
        if self._failed:
            text += " · could not read " + "; ".join(f"{c} ({m})" for c, m in sorted(self._failed.items()))
        self.status.setText(text)
