import time

from PySide6.QtCore import QElapsedTimer, QItemSelectionModel, QSortFilterProxyModel, Qt, Signal, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QStackedWidget, QTableView, QToolButton, QVBoxLayout, QWidget)

from ..k8s.clone import COPYABLE
from ..k8s.resources import DELETABLE_KINDS
from ..k8s.timing import timings
from .customizations import ANNOTATION_KINDS
from .loading_pane import LoadingPane
from .table_model import TableModel


LOG_SOURCES = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Service"}  # have a selector: their pods' logs together
SCALABLE = {"Deployment", "ReplicaSet", "StatefulSet"}
RESTARTABLE = {"Deployment", "StatefulSet", "DaemonSet"}  # rollout restart: a ReplicaSet has no rollout of its own
NOT_CUSTOMIZABLE = {"Container", "CustomResource", "CRDGroup", "HelmRelease"}  # per-pod container rows and dynamic CR columns keep fixed columns


class ResourceTable(QWidget):
    selected = Signal(object)  # row dataclass
    edit_requested = Signal(str, object)  # kind, row dataclass
    scale_requested = Signal(str, object)
    restart_requested = Signal(str, object)
    history_requested = Signal(str, object)  # a Deployment's rollout history / undo
    refresh_requested = Signal()  # right-click > Refresh
    refresh_mode_requested = Signal(bool)  # right-click > Refresh automatically (checked = True)
    port_forward_requested = Signal(str, object)  # a Pod or Service: forward one of its ports to this computer
    copy_files_requested = Signal(object)  # a Pod: copy files between this computer and a container
    goto_requested = Signal(str, str)  # "node" or "namespace", and its name: the row's right-click "Go to ..." entries
    action_requested = Signal(str, object)  # action name, row dataclass
    new_requested = Signal()
    more_requested = Signal()  # the Load more button of a paged list
    customize_requested = Signal(str)  # kind whose label columns the user wants to edit
    visible_changed = Signal()  # the set of rows on screen may have changed (scroll, filter, sort, resize, new rows)
    rbac_requested = Signal(str, object)  # a ServiceAccount row: what can it do?
    multi_logs_requested = Signal(str, object)  # kind, a row (workload or Service) or a list of Pod rows
    duplicate_requested = Signal(str, object)  # kind, row: create a new object starting from this one
    compare_requested = Signal(str, list)  # kind, the two selected rows
    bulk_requested = Signal(str, str, list)  # kind, action name, selected row dataclasses (two or more)
    rows_incoming = Signal(list)  # new rows are about to go in: plugins put the values they already know on them
    rows_set = Signal(list)  # new rows are in the table (plugins ask for their columns when this fires)
    plugin_action_requested = Signal(str, str, list)  # plugin id, action key, selected row objects

    def __init__(self, columns, kind, editable=True, customizable=None):
        super().__init__()
        self.deletable = False  # set by the workspace for editable custom resources (the Gateway API kinds)
        self.refresh_mode = None  # () -> True when this list reloads only on request; set by the workspace (None = no such menu)
        self._loaded_at = ""
        self.permission_check = None  # (kind, action, row) -> True / False / None, set by the workspace; False adds a note to a menu entry
        if customizable is None:
            customizable = kind not in NOT_CUSTOMIZABLE
        self.kind = kind
        self.editable = editable
        self.core_columns = list(columns)
        self.extra_columns = []  # columns added by plugins (see set_extra_columns)
        self.base_columns = list(columns)
        self._label_args = None
        self.plugin_actions = None  # callable(kind, rows) -> [(plugin name, plugin id, [Action])], set by the workspace
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
        self.view.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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
        top = QHBoxLayout()
        self._top = top
        top.addWidget(self.filter, 1)
        self.new_button = None  # see enable_new()
        self.paging_note = ""  # see set_paging()
        self.more_button = QPushButton("Load more")
        self.more_button.setVisible(False)
        self.more_button.clicked.connect(self.more_requested.emit)
        top.addWidget(self.more_button)
        self.customize_button = None
        if customizable:  # Node and Pod lists: pick which labels become columns
            self.customize_button = QPushButton("Columns…")
            what = "label and annotation" if kind in ANNOTATION_KINDS else "label"
            self.customize_button.setToolTip(f"Show, hide, order and add {what} columns for {kind} lists")
            self.customize_button.clicked.connect(lambda: self.customize_requested.emit(kind))
            top.addWidget(self.customize_button)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.status)
        # Long lists (Nodes, Pods) can take a while the first time: while there is nothing to show yet, a large
        # message takes the place of the empty table. Reloads with rows on screen only use the status line.
        self.loading_page = LoadingPane()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.loading_page)
        lay.addWidget(self.stack, 1)

    def set_loading(self):
        self._load_clock.start()
        self.status.setText("Loading…")
        if not self.model.rowCount():
            self.loading_page.show_busy("Loading…")
            self.stack.setCurrentWidget(self.loading_page)
        self._load_timer.start()

    def _loading_tick(self):
        text = f"Loading… {self._load_clock.elapsed() // 1000}s"
        self.status.setText(text)
        if self.stack.currentWidget() is self.loading_page:
            self.loading_page.show_busy(text)

    def set_error(self, message):
        self._load_timer.stop()
        self.status.setText(f"Load failed: {(str(message).splitlines() or [""])[0]}")
        if not self.model.rowCount():  # nothing else on screen: say it where the list would be
            self.loading_page.show_failed(f"Load failed: {message}")
            self.stack.setCurrentWidget(self.loading_page)

    def enable_saved_filters(self, saved, context):
        """Add a ★ Filters button next to the filter box: keep the current filter text under a name, apply or forget one. Kept
        per cluster and list kind in `saved` (a SavedFilters)."""
        if getattr(self, "saved_button", None) is not None:
            return
        self._saved_filters, self._saved_context = saved, context
        self.saved_button = QToolButton()
        self.saved_button.setText("★ Filters")
        self.saved_button.setToolTip("Save the current filter, or use one you saved for this cluster")
        self.saved_button.setPopupMode(QToolButton.InstantPopup)
        self.saved_menu = QMenu(self.saved_button)
        self.saved_menu.aboutToShow.connect(self._fill_saved_filters)
        self.saved_button.setMenu(self.saved_menu)
        self._top.insertWidget(1, self.saved_button)

    def _fill_saved_filters(self):
        menu = self.saved_menu
        menu.clear()
        save = menu.addAction("Save current filter as…", self._save_filter)
        save.setEnabled(bool(self.filter.text().strip()))
        entries = self._saved_filters.for_list(self._saved_context, self.kind)
        if entries:
            menu.addSeparator()
        for entry in entries:
            menu.addAction(f"{entry['name']}: {entry['text']}", lambda e=entry: self.filter.setText(e["text"]))
        if entries:
            forget = menu.addMenu("Forget")
            for entry in entries:
                forget.addAction(entry["name"], lambda e=entry: self._forget_filter(e))

    def _save_filter(self):
        name, ok = QInputDialog.getText(self, "Save filter", f"Name for the filter “{self.filter.text().strip()}”:")
        if not ok or not name.strip():
            return
        try:
            self._saved_filters.add(self._saved_context, self.kind, name, self.filter.text())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Save filter", str(e))

    def _forget_filter(self, entry):
        try:
            self._saved_filters.remove(entry)
        except OSError as e:
            QMessageBox.warning(self, "Save filter", str(e))

    def enable_new(self):
        """Mark this kind as creatable here: the right-click menu (also on an empty list) offers "New <kind>…". There is deliberately no
        button beside the filter: next to a filter box "New…" read as "new filter". `new_button` is only that mark now (None = not
        creatable), kept under its old name because the callers and tests ask `new_button is not None`."""
        if self.new_button is None:
            self.new_button = True

    def set_rows(self, rows):
        self._load_timer.stop()
        self.stack.setCurrentWidget(self.view)
        self.rows_incoming.emit(rows)  # before the columns are measured: a refresh must not blank or shrink plugin columns
        kept = self._selection_to_keep()
        began = time.perf_counter() if timings.enabled else 0.0
        self.model.set_rows(rows)
        self.view.resizeColumnsToContents()
        if timings.enabled:
            timings.record("gui", f"table.{self.kind}", time.perf_counter() - began, rows=len(rows))
        self._restore_selection(*kept)
        self._loaded_at = time.strftime("%H:%M:%S")
        self.show_status()
        self.rows_set.emit(self.model.rows)
        self._apply_goto(final=True)

    # ---- "Go to ..." from another list: select a named row, now or as soon as the list has loaded -----------
    def select_when_loaded(self, name, namespace=""):
        """Select (and scroll to) the row with this name, so Details shows it. When the list has not loaded it yet the wish is
        kept for the next load; after that load it is dropped, found or not, so a later reload never jumps."""
        self._goto = (name, namespace)
        self._apply_goto(final=False)

    def _apply_goto(self, final):
        wish = getattr(self, "_goto", None)
        if wish is None:
            return
        name, namespace = wish
        for source_row, row in enumerate(self.model.rows):
            if row.name == name and (not namespace or getattr(row, "namespace", "") == namespace):
                index = self.proxy.mapFromSource(self.model.index(source_row, 0))
                if index.isValid():  # a row hidden by the filter cannot be selected
                    self._goto = None
                    self.view.selectRow(index.row())
                    self.view.scrollTo(index)
                    return
        if final:
            self._goto = None

    def show_status(self):
        """The line under the list: the row count, and for a list that reloads only on request when it was loaded."""
        count = self.model.rowCount()
        text = f"{count:,} rows" if count else "No resources found"
        if self.refresh_mode is not None and self.refresh_mode() and self._loaded_at:
            text += f" · manual refresh, loaded {self._loaded_at}"
        if self.paging_note:
            text += f" · {self.paging_note}"
        self.status.setText(text)

    def set_paging(self, note, more):
        """A list that is read a page at a time (the Pods list of a huge cluster): `note` is added to the status line and the
        Load more button shows while `more` is true. `set_paging("", False)` goes back to a plain list."""
        self.paging_note = note
        self.more_button.setVisible(bool(more))
        self.more_button.setEnabled(True)
        self.show_status()

    def append_rows(self, rows):
        """Add rows to what is shown (the next page), keeping the selection and scroll position like any reload."""
        self.set_rows(list(self.model.rows) + list(rows))

    @staticmethod
    def _row_key(row):
        """What identifies a row across reloads: its uid, else namespace/name."""
        uid = ((getattr(row, "raw", None) or {}).get("metadata") or {}).get("uid")
        return uid or f"{getattr(row, 'namespace', '')}/{getattr(row, 'name', '')}"

    def _selection_to_keep(self):
        """(selected keys, current key, scroll position): a reload replaces every row, which would clear all three."""
        selection = self.view.selectionModel()
        keys = {self._row_key(self.model.rows[self.proxy.mapToSource(i).row()]) for i in selection.selectedRows()}
        current = selection.currentIndex()
        current_key = (self._row_key(self.model.rows[self.proxy.mapToSource(current).row()])
                       if current.isValid() else None)
        return keys, current_key, self.view.verticalScrollBar().value()

    def _restore_selection(self, keys, current_key, scroll):
        """Select the same objects again, silently: nothing changed for the user, so the Details pane (and its
        decoded secret keys) must not be redrawn or cleared."""
        if not keys and current_key is None:
            return
        selection = self.view.selectionModel()
        selection.blockSignals(True)
        try:
            for source_row, row in enumerate(self.model.rows):
                key = self._row_key(row)
                if key not in keys and key != current_key:
                    continue
                index = self.proxy.mapFromSource(self.model.index(source_row, 0))
                if not index.isValid():  # hidden by the filter
                    continue
                if key in keys:
                    selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                if key == current_key:
                    selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        finally:
            selection.blockSignals(False)
        self.view.verticalScrollBar().setValue(scroll)

    def fit_plugin_columns(self):
        """Widen the plugin columns to their values (done when values arrive, not on every refresh)."""
        for index, column in enumerate(self.model.columns):
            if column[1].startswith("plugin:"):
                self.view.resizeColumnToContents(index)

    def set_extra_columns(self, columns):
        """Columns added by plugins, after the built-in ones. They take part in the saved layout (order, hidden)
        and the Columns… dialog like any built-in column."""
        columns = list(columns)
        if columns == self.extra_columns or self.kind in NOT_CUSTOMIZABLE:
            return
        self.extra_columns = columns
        self.base_columns = self.core_columns + columns
        self.set_label_columns(*(self._label_args or ([],)))

    def set_label_columns(self, keys, headings=None, layout=None, annotations=(), annotation_headings=None):
        """Apply label and annotation columns plus the saved built-in column order/visibility
        (layout: {"order", "hidden"}). Annotation columns follow the label columns."""
        if self.kind in NOT_CUSTOMIZABLE:
            return
        self._label_args = (keys, headings, layout, annotations, annotation_headings)
        layout = layout or {}
        first, rest = self.base_columns[:1], self.base_columns[1:]  # the first column (Name) never moves or hides
        order = layout.get("order", [])
        rank = {attr: i for i, attr in enumerate(order)}
        rest = sorted(rest, key=lambda col: rank.get(col[1], len(order)))  # stable: unknown ones keep their place
        columns = first + [col for col in rest if col[1] not in layout.get("hidden", [])]
        anchor = "roles" if self.kind == "Node" else "name"
        insert_at = next((i + 1 for i, col in enumerate(columns) if col[1] == anchor), 1)
        headings = headings or {}
        columns[insert_at:insert_at] = [
            (headings.get(key) or ("NodePool" if key == "karpenter.sh/nodepool" else key), f"label:{key}") for key in keys]
        notes = annotation_headings or {}
        columns[insert_at + len(keys):insert_at + len(keys)] = [
            (notes.get(key) or key, f"annotation:{key}") for key in annotations]
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
        if n == 2 and all(getattr(r, "raw", None) for r in rows):  # looking only, so it is always offered
            menu.addAction("Compare these two…", lambda: self.compare_requested.emit(self.kind, list(rows)))
            menu.addSeparator()
        if self.kind in SCALABLE:
            menu.addAction(f"Scale {n} {self.kind}s…", bulk("scale"))
        if self.kind in RESTARTABLE:
            menu.addAction(f"Restart {n} {self.kind}s…", bulk("restart"))
        if self.kind in DELETABLE_KINDS:
            menu.addAction(f"Delete {n} {self.kind}s…", bulk("delete"))
        if self.kind == "Pod":
            menu.addAction(f"Logs of {n} pods…", lambda: self.multi_logs_requested.emit("Pod", list(rows)))
            menu.addAction(f"Terminate {n} pods…", bulk("terminate"))
            menu.addAction(f"Force terminate {n} pods…", bulk("force-terminate"))
        if self.kind == "Node":
            menu.addAction(f"Cordon {n} nodes…", bulk("cordon"))
            menu.addAction(f"Uncordon {n} nodes…", bulk("uncordon"))
            menu.addSeparator()
            menu.addAction(f"Delete {n} nodes…", bulk("delete-node"))
            menu.addAction(f"Force delete {n} nodes…", bulk("force-delete-node"))

    NOT_ALLOWED = "  (not allowed for you)"

    def _hint(self, text, action, row, kind=None):
        """`text`, with a note when the rules the server reported say this user may not do `action` here. It is only a note:
        the entry stays clickable (the rules can be incomplete) and the server has the last word."""
        check = self.permission_check
        if check is not None and check(kind or self.kind, action, row) is False:
            return text + self.NOT_ALLOWED
        return text

    def _goto_menu(self, menu, row):
        """'Go to node ...' for a pod that has one, 'Go to namespace ...' for any namespaced object (a Namespace's own list
        has its Show pods entry instead). Navigation only: it never changes anything, so it is also offered read-only."""
        node = getattr(row, "node", "") if self.kind == "Pod" else ""
        namespace = getattr(row, "namespace", "") if self.kind not in ("Namespace", "Node") else ""
        if not node and not namespace:
            return
        menu.addSeparator()
        if node:
            menu.addAction(f"Go to node {node}", lambda: self.goto_requested.emit("node", node))
        if namespace:
            menu.addAction(f"Go to namespace {namespace}", lambda: self.goto_requested.emit("namespace", namespace))

    def _plugin_menu(self, menu, rows):
        """Entries plugins add for these rows: one submenu per plugin, or flat for a plugin with a single action."""
        groups = self.plugin_actions(self.kind, rows) if self.plugin_actions is not None else []
        if not groups:
            return
        menu.addSeparator()
        for plugin_name, plugin_id, actions in groups:
            def add(target, action, plugin_id=plugin_id, plugin_name=plugin_name):
                entry = target.addAction(("⚠ " if action.dangerous else "") + action.title,
                                         lambda: self.plugin_action_requested.emit(plugin_id, action.key, rows))
                entry.setToolTip(plugin_name)
            if len(actions) == 1:
                add(menu, actions[0])
            else:
                submenu = menu.addMenu(plugin_name)
                for action in actions:
                    add(submenu, action)

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            if not self.view.selectionModel().isRowSelected(idx.row(), idx.parent()):
                self.view.selectRow(idx.row())  # right-click outside the selection acts on that row alone
            rows = self.selected_rows()
            if len(rows) > 1:
                self._bulk_menu(menu, rows)
                self._plugin_menu(menu, rows)
                if not menu.isEmpty():
                    menu.exec(self.view.viewport().mapToGlobal(pos))
                return
            row = self.model.rows[self.proxy.mapToSource(idx).row()]
            if self.editable:
                menu.addAction(self._hint("Edit…", "edit", row), lambda: self.edit_requested.emit(self.kind, row))
            if self.kind in COPYABLE and self.editable:
                menu.addAction(self._hint("Copy as new…", "create", row), lambda: self.duplicate_requested.emit(self.kind, row))
            if self.kind == "ServiceAccount":
                menu.addAction("Effective permissions…", lambda: self.rbac_requested.emit(self.kind, row))
            if self.kind in LOG_SOURCES:
                menu.addAction("Logs of all pods…", lambda: self.multi_logs_requested.emit(self.kind, row))
            if self.kind in SCALABLE:
                menu.addAction(self._hint("Scale…", "scale", row), lambda: self.scale_requested.emit(self.kind, row))
            if self.kind in RESTARTABLE:
                menu.addAction(self._hint("Restart…", "restart", row), lambda: self.restart_requested.emit(self.kind, row))
            if self.kind == "CronJob":
                menu.addAction("Run now…", lambda: self.action_requested.emit("cronjob-run", row))
                suspended = str(getattr(row, "suspend", "")) == "True"
                menu.addAction(self._hint("Resume…" if suspended else "Suspend…", "cronjob-suspend", row),
                               lambda: self.action_requested.emit("cronjob-resume" if suspended else "cronjob-suspend", row))
            if self.kind == "Deployment":
                menu.addAction("Rollout history / Undo…", lambda: self.history_requested.emit(self.kind, row))
            if self.kind in DELETABLE_KINDS or self.deletable:
                menu.addAction(self._hint("Delete…", "delete", row), lambda: self.action_requested.emit("delete", row))
            if self.kind in ("Pod", "Service"):
                menu.addAction("Port forward…", lambda: self.port_forward_requested.emit(self.kind, row))
            if self.kind == "Pod":
                menu.addAction("Browse files…", lambda: self.action_requested.emit("browse-files", row))
                menu.addAction("Copy files…", lambda: self.copy_files_requested.emit(row))
            if self.kind == "PersistentVolumeClaim":
                menu.addAction("Browse files…", lambda: self.action_requested.emit("browse-files", row))
            if self.kind == "Node":
                menu.addSeparator()
                menu.addAction("List pods on this node", lambda: self.action_requested.emit("node-pods", row))
                cordoned = (row.raw.get("spec") or {}).get("unschedulable", False)
                action = "uncordon" if cordoned else "cordon"
                menu.addAction(self._hint("Uncordon…" if cordoned else "Cordon…", "cordon", row),
                               lambda: self.action_requested.emit(action, row))
                menu.addAction(self._hint("Drain…", "cordon", row), lambda: self.action_requested.emit("drain", row))
                menu.addAction("Shell (nsenter)…", lambda: self.action_requested.emit("node-shell", row))
                menu.addSeparator()
                menu.addAction(self._hint("Delete node…", "delete-node", row), lambda: self.action_requested.emit("delete-node", row))
                menu.addAction(self._hint("Force delete node…", "delete-node", row),
                               lambda: self.action_requested.emit("force-delete-node", row))
            if self.kind == "Container":
                menu.addAction("Logs", lambda: self.action_requested.emit("logs", row))
                menu.addAction("Connect (shell)…", lambda: self.action_requested.emit("shell", row))
                menu.addAction("Attach…", lambda: self.action_requested.emit("attach", row))
            if self.kind == "Pod":
                menu.addSeparator()
                menu.addAction(self._hint("Logs", "logs", row), lambda: self.action_requested.emit("logs", row))
                menu.addAction(self._hint("Connect (shell)…", "shell", row), lambda: self.action_requested.emit("shell", row))
                menu.addAction(self._hint("Attach…", "attach", row), lambda: self.action_requested.emit("attach", row))
                menu.addAction(self._hint("Debug (ephemeral container)…", "debug", row), lambda: self.action_requested.emit("debug", row))
                if ((getattr(row, "raw", None) or {}).get("spec") or {}).get("ephemeralContainers"):
                    menu.addAction(self._hint("Remove debug containers (recreate pod)…", "terminate", row),
                                   lambda: self.action_requested.emit("remove-debug", row))
                menu.addSeparator()
                menu.addAction(self._hint("Terminate…", "terminate", row), lambda: self.action_requested.emit("terminate", row))
                menu.addAction(self._hint("Force terminate…", "terminate", row),
                               lambda: self.action_requested.emit("force-terminate", row))
        if idx.isValid():
            self._goto_menu(menu, self.model.rows[self.proxy.mapToSource(idx).row()])
        if self.kind == "CustomResourceDefinition" and idx.isValid():
            menu.addAction("Browse instances", lambda: self.action_requested.emit("open-crd", row))
        if self.kind == "HelmRelease" and idx.isValid():
            menu.addAction("Details / history…", lambda: self.action_requested.emit("helm-details", row))
            menu.addAction("Uninstall…", lambda: self.action_requested.emit("helm-uninstall", row))
        if self.kind == "CRDGroup" and idx.isValid():
            menu.addAction("Show CRDs of this group", lambda: self.action_requested.emit("open-crd-group", row))
        if self.kind == "Namespace":
            if idx.isValid():
                menu.addAction("Show pods", lambda: self.action_requested.emit("open-namespace", row))
                menu.addAction("Export diagnostics…", lambda: self.action_requested.emit("diagnostics", row))
            menu.addAction("New namespace…", self.new_requested.emit)
        if self.new_button is not None:
            menu.addAction(self._hint(f"New {self.kind}…", "create", None), self.new_requested.emit)
        if self.refresh_mode is not None:
            if not menu.isEmpty():
                menu.addSeparator()
            menu.addAction("Refresh", self.refresh_requested.emit)
            auto = menu.addAction("Refresh automatically")
            auto.setCheckable(True)
            auto.setChecked(not self.refresh_mode())
            auto.setToolTip("Off: this list reloads only when you press Refresh")
            auto.triggered.connect(lambda checked: self.refresh_mode_requested.emit(bool(checked)))
        if idx.isValid():  # plugin entries come last, after the built-in ones
            self._plugin_menu(menu, [self.model.rows[self.proxy.mapToSource(idx).row()]])
        if not menu.isEmpty():
            menu.exec(self.view.viewport().mapToGlobal(pos))

    def _on_row(self, current, _prev):
        if current.isValid():
            src = self.proxy.mapToSource(current)
            row = self.model.rows[src.row()]
            try:
                row.table_kind = self.kind  # what kind of object this row is: the list manifests carry no `kind`, Details needs it
            except AttributeError:  # a row type without room for it simply has no Related view
                pass
            self.selected.emit(row)

    DOUBLE_CLICK = {"Pod": "containers", "Container": "logs", "Node": "node-pods", "Namespace": "open-namespace",
                    "CustomResourceDefinition": "open-crd", "CRDGroup": "open-crd-group",
                    "HelmRelease": "helm-details"}

    def _on_double_click(self, index):
        action = self.DOUBLE_CLICK.get(self.kind)
        if action and index.isValid():
            self.action_requested.emit(action, self.model.rows[self.proxy.mapToSource(index).row()])
