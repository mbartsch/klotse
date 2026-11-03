from PySide6.QtCore import QElapsedTimer, QSortFilterProxyModel, Qt, Signal, QTimer
from PySide6.QtWidgets import QAbstractItemView, QLabel, QLineEdit, QMenu, QTableView, QVBoxLayout, QWidget

from .table_model import TableModel


SCALABLE = {"Deployment", "ReplicaSet", "StatefulSet"}


class ResourceTable(QWidget):
    selected = Signal(object)  # row dataclass
    edit_requested = Signal(str, object)  # kind, row dataclass
    scale_requested = Signal(str, object)
    action_requested = Signal(str, object)  # action name, row dataclass
    new_requested = Signal()
    visible_changed = Signal()  # the set of rows on screen may have changed (scroll, filter, sort, resize, new rows)
    bulk_requested = Signal(str, str, list)  # kind, action name, selected row dataclasses (two or more)

    def __init__(self, columns, kind, editable=True):
        super().__init__()
        self.kind = kind
        self.editable = editable
        self.base_columns = list(columns)
        self.model = TableModel(columns, search_metadata=kind == "Node")
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.EditRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        if kind == "Node":
            self.proxy.setFilterRole(TableModel.SearchRole)
        self.filter = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        if kind == "Node":
            self.filter.setPlaceholderText("Filter nodes, labels and annotations…")
        self._filter_timer = QTimer(self, interval=200, singleShot=True)
        self._filter_timer.timeout.connect(lambda: self.proxy.setFilterFixedString(self.filter.text()))
        self.filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.status = QLabel("Not loaded")
        self.status.setWordWrap(True)
        self._load_clock = QElapsedTimer()
        self._load_timer = QTimer(self, interval=1000)
        self._load_timer.timeout.connect(self._loading_tick)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        # Preserve API order initially; sorting every row through Python/Qt on
        # first load blocks the event loop. Header clicks still enable sorting.
        self.view.sortByColumn(-1, Qt.AscendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)  # Ctrl/Shift-click for bulk actions
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.horizontalHeader().setStretchLastSection(True)
        # Column sizing runs on the GUI thread. Sample a bounded number of rows.
        self.view.horizontalHeader().setResizeContentsPrecision(100)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(26)  # dense rows
        self.view.setShowGrid(False)
        self._visible_timer = QTimer(self, interval=250, singleShot=True)
        self._visible_timer.timeout.connect(self.visible_changed.emit)
        self.view.verticalScrollBar().valueChanged.connect(lambda _: self._visible_timer.start())
        for signal in (self.proxy.modelReset, self.proxy.layoutChanged, self.proxy.rowsInserted, self.proxy.rowsRemoved):
            signal.connect(lambda *_: self._visible_timer.start())
        self.view.selectionModel().currentRowChanged.connect(self._on_row)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.status)
        lay.addWidget(self.view)

    def set_loading(self):
        self._load_clock.start()
        self.status.setText("Loading…")
        self._load_timer.start()

    def _loading_tick(self):
        self.status.setText(f"Loading… {self._load_clock.elapsed() // 1000}s")

    def set_error(self, message):
        self._load_timer.stop()
        self.status.setText(f"Load failed: {message}")

    def set_rows(self, rows):
        self._load_timer.stop()
        self.model.set_rows(rows)
        self.view.resizeColumnsToContents()
        self.status.setText(f"{self.model.rowCount():,} rows" if self.model.rowCount() else "No resources found")

    def set_label_columns(self, keys, headings=None):
        if self.kind not in ("Node", "Pod"):
            return
        columns = list(self.base_columns)
        anchor = "roles" if self.kind == "Node" else "name"
        insert_at = next((i + 1 for i, col in enumerate(columns) if col[1] == anchor), len(columns))
        headings = headings or {}
        columns[insert_at:insert_at] = [
            (headings.get(key) or ("NodePool" if key == "karpenter.sh/nodepool" else key), f"label:{key}") for key in keys]
        if columns == self.model.columns:
            return
        header = self.view.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_attr = self.model.columns[sort_column][1] if 0 <= sort_column < len(self.model.columns) else None
        sort_order = header.sortIndicatorOrder()
        self.model.set_columns(columns)
        new_sort = next((i for i, col in enumerate(columns) if col[1] == sort_attr), -1)
        self.view.sortByColumn(new_sort, sort_order)
        self.view.resizeColumnsToContents()

    def set_header_widget(self, widget):
        """Put a widget (e.g. an action bar) above the filter box."""
        self.layout().insertWidget(0, widget)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._visible_timer.start()

    def visible_rows(self):
        """Rows currently on screen (scrolled into the viewport, after filtering and sorting)."""
        if not self.isVisible() or self.proxy.rowCount() == 0:
            return []
        first = self.view.rowAt(0)
        if first < 0:
            return []
        last = self.view.rowAt(self.view.viewport().height() - 1)
        if last < 0:
            last = self.proxy.rowCount() - 1
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(r, 0)).row()] for r in range(first, last + 1)]

    def selected_rows(self):
        return [self.model.rows[self.proxy.mapToSource(i).row()] for i in self.view.selectionModel().selectedRows()]

    def _bulk_menu(self, menu, rows):
        n = len(rows)
        bulk = lambda action: (lambda: self.bulk_requested.emit(self.kind, action, rows))
        if self.kind in SCALABLE:
            menu.addAction(f"Scale {n} {self.kind}s…", bulk("scale"))
        if self.kind == "Pod":
            menu.addAction(f"Terminate {n} pods…", bulk("terminate"))
            menu.addAction(f"Force terminate {n} pods…", bulk("force-terminate"))
        if self.kind == "Node":
            menu.addAction(f"Cordon {n} nodes…", bulk("cordon"))
            menu.addAction(f"Uncordon {n} nodes…", bulk("uncordon"))
            menu.addSeparator()
            menu.addAction(f"Delete {n} nodes…", bulk("delete-node"))
            menu.addAction(f"Force delete {n} nodes…", bulk("force-delete-node"))

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            if not self.view.selectionModel().isRowSelected(idx.row(), idx.parent()):
                self.view.selectRow(idx.row())  # right-click outside the selection acts on that row alone
            rows = self.selected_rows()
            if len(rows) > 1:
                self._bulk_menu(menu, rows)
                if not menu.isEmpty():
                    menu.exec(self.view.viewport().mapToGlobal(pos))
                return
            row = self.model.rows[self.proxy.mapToSource(idx).row()]
            if self.editable:
                menu.addAction("Edit…", lambda: self.edit_requested.emit(self.kind, row))
            if self.kind in SCALABLE:
                menu.addAction("Scale…", lambda: self.scale_requested.emit(self.kind, row))
            if self.kind == "Node":
                menu.addSeparator()
                menu.addAction("List pods on this node", lambda: self.action_requested.emit("node-pods", row))
                cordoned = (row.raw.get("spec") or {}).get("unschedulable", False)
                action = "uncordon" if cordoned else "cordon"
                menu.addAction("Uncordon…" if cordoned else "Cordon…",
                               lambda: self.action_requested.emit(action, row))
                menu.addAction("Drain…", lambda: self.action_requested.emit("drain", row))
                menu.addAction("Shell (nsenter)…", lambda: self.action_requested.emit("node-shell", row))
                menu.addSeparator()
                menu.addAction("Delete node…", lambda: self.action_requested.emit("delete-node", row))
                menu.addAction("Force delete node…", lambda: self.action_requested.emit("force-delete-node", row))
            if self.kind == "Container":
                menu.addAction("Logs", lambda: self.action_requested.emit("logs", row))
                menu.addAction("Connect (shell)…", lambda: self.action_requested.emit("shell", row))
                menu.addAction("Attach…", lambda: self.action_requested.emit("attach", row))
            if self.kind == "Pod":
                menu.addSeparator()
                menu.addAction("Logs", lambda: self.action_requested.emit("logs", row))
                menu.addAction("Connect (shell)…", lambda: self.action_requested.emit("shell", row))
                menu.addAction("Attach…", lambda: self.action_requested.emit("attach", row))
                menu.addSeparator()
                menu.addAction("Terminate…", lambda: self.action_requested.emit("terminate", row))
                menu.addAction("Force terminate…", lambda: self.action_requested.emit("force-terminate", row))
        if self.kind == "CustomResourceDefinition" and idx.isValid():
            menu.addAction("Browse instances", lambda: self.action_requested.emit("open-crd", row))
        if self.kind == "Namespace":
            if idx.isValid():
                menu.addAction("Show pods", lambda: self.action_requested.emit("open-namespace", row))
            menu.addAction("New namespace…", self.new_requested.emit)
        if not menu.isEmpty():
            menu.exec(self.view.viewport().mapToGlobal(pos))

    def _on_row(self, current, _prev):
        if current.isValid():
            src = self.proxy.mapToSource(current)
            self.selected.emit(self.model.rows[src.row()])

    DOUBLE_CLICK = {"Pod": "containers", "Container": "logs", "Node": "node-pods", "Namespace": "open-namespace",
                    "CustomResourceDefinition": "open-crd"}

    def _on_double_click(self, index):
        action = self.DOUBLE_CLICK.get(self.kind)
        if action and index.isValid():
            self.action_requested.emit(action, self.model.rows[self.proxy.mapToSource(index).row()])
