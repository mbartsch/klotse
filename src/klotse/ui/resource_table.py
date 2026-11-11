from dataclasses import dataclass
import re
import time

from PySide6.QtCore import QElapsedTimer, QItemSelectionModel, QStringListModel, QSortFilterProxyModel, Qt, Signal, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QCompleter, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QStackedWidget, QTableView, QToolButton, QVBoxLayout, QWidget)

from ..k8s.clone import COPYABLE
from ..k8s.resources import DELETABLE_KINDS
from ..k8s.timing import timings
from . import filter_expr, theme
from .action_icons import action_icon
from .customizations import ANNOTATION_KINDS
from .loading_pane import LoadingPane
from .table_model import TableModel, age_sort_value


METRIC_KINDS = {"Pod", "Node", "Deployment", "StatefulSet", "DaemonSet"}  # have a usage history chart
LOG_SOURCES = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Service", "CronJob"}  # selector or CronJob ownership
SCALABLE = {"Deployment", "ReplicaSet", "StatefulSet"}
RESTARTABLE = {"Deployment", "StatefulSet", "DaemonSet"}  # rollout restart: a ReplicaSet has no rollout of its own
NOT_CUSTOMIZABLE = {"Container", "CustomResource", "CRDGroup", "HelmRelease"}  # per-pod container rows and dynamic CR columns keep fixed columns


FILTER_HELP = "\n".join([
    "Type text to match it in any column. Or write a comparison on columns:",
    "  Restarts > 3        Status ~ crash        Name = web-*        Namespace != kube-system",
    "  CPULim = 2*CPUReq   CPUUsed > 500m        MemLim < 1Gi        Age > 2h",
    "  spec.replicas >= 2   status.phase = Running   spec.suspend = true",
    "Type a column or spec/status path to see suggestions; accept one to complete the word at the cursor.",
    "spec/status paths use exact manifest keys and raw scalar values, without column unit conversion.",
    "Use zero-based list indices: spec.containers.0.image = 'nginx:*' (no brackets or wildcards in paths).",
    "Booleans compare as true/false text. Missing, null, empty, object and list values match nothing.",
    "  app = web (a label)  Kind = Pod            a and b, a or b, (…) to group",
    "= and != ignore case and take * as a wildcard; ~ is a regular expression. Blank values match nothing."])


@dataclass(frozen=True)
class RowAction:
    key: str
    title: str
    callback: object
    primary: bool = False
    dangerous: bool = False


class _FilterProxy(QSortFilterProxyModel):
    """The table's proxy: plain text filtering as before, or a filter expression (see filter_expr) evaluated on the row's values."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.expression = None
        self.resolver = None  # (row, name) -> (value, unit) | None, set by the table

    def set_expression(self, expression):
        if hasattr(self, "beginFilterChange"):  # Qt 6.9+: invalidateFilter() is deprecated
            self.beginFilterChange()
            self.expression = expression
            self.endFilterChange()
        else:
            self.expression = expression
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if self.expression is None:
            return super().filterAcceptsRow(source_row, source_parent)
        row = self.sourceModel().rows[source_row]
        return self.expression.matches(lambda name: self.resolver(row, name))


class _FilterCompleter(QCompleter):
    """Complete the word at the cursor, leaving the rest of a filter expression intact."""

    def splitPath(self, path):
        cursor = self.widget().cursorPosition() if self.widget() else len(path)
        match = re.search(r"[A-Za-z0-9_.:-]*$", path[:cursor])
        return [match.group(0) if match else ""]


class ResourceTable(QWidget):
    selected = Signal(object)  # row dataclass
    edit_requested = Signal(str, object)  # kind, row dataclass
    scale_requested = Signal(str, object)
    restart_requested = Signal(str, object)
    history_requested = Signal(str, object)  # a Deployment's rollout history / undo
    refresh_requested = Signal()  # right-click > Refresh
    permissions_requested = Signal()  # actionable permission refusal state
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
    metrics_requested = Signal(str, object)  # kind, row: show the usage history chart
    metrics_many_requested = Signal(str, object)  # kind, rows picked together: one usage chart with a line per object
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
        self._last_error = ""
        self._sized_columns = False
        self._column_widths = {}  # widths keyed by attribute, including temporarily hidden plugin columns
        self._has_loaded = False
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
        self.context_actions = None  # optional (menu, selected rows) builder for views with their own supported actions
        self.model = TableModel(columns, search_metadata=kind == "Node")
        self.proxy = _FilterProxy(self)
        self.proxy.resolver = self._resolve
        self._columns_seen = (None, {})
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.EditRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        if kind == "Node":
            self.proxy.setFilterRole(TableModel.SearchRole)
        self.filter = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        self.filter.setAccessibleName(f"Filter {kind} list")
        self._completion_model = QStringListModel(self)
        self._completer = _FilterCompleter(self._completion_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.popup().setFocusPolicy(Qt.NoFocus)
        self._completer.activated.connect(self._insert_filter_completion)
        self.filter.setCompleter(self._completer)
        self.filter.textEdited.connect(self._show_filter_completions)
        if kind == "Node":
            self.filter.setPlaceholderText("Filter nodes, labels and annotations…")
        self._filter_timer = QTimer(self, interval=200, singleShot=True)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.filter.setToolTip(FILTER_HELP)
        self.filter_note = QLabel()
        self.filter_note.setWordWrap(True)
        self.filter_note.setVisible(False)
        self.filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.filter.textChanged.connect(lambda _: QTimer.singleShot(0, self._update_empty_state))
        self.status = QLabel("Not loaded")
        self.status.setWordWrap(True)
        self.status.setAccessibleName(f"{kind} list status")
        self.selection_count = QLabel()
        self.selection_count.setAccessibleName("Selected row count")
        self.selection_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.selection_count.setText("0 selected")
        self._load_clock = QElapsedTimer()
        self._load_timer = QTimer(self, interval=1000)
        self._load_timer.timeout.connect(self._loading_tick)
        self.view = QTableView()
        self.view.setObjectName("resourceGrid")
        self.view.setAccessibleName(f"{kind} list")
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        # Preserve API order initially; sorting every row through Python/Qt on
        # first load blocks the event loop. Header clicks still enable sorting.
        self.view.sortByColumn(-1, Qt.AscendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)  # Ctrl/Shift-click for bulk actions
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Stretching the last section would silently change its width when the
        # window or the set of plugin columns changes.
        self.view.horizontalHeader().setStretchLastSection(False)
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
        self.view.selectionModel().selectionChanged.connect(self._update_selection_count)
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
<<<<<<< HEAD
        fit_button = QPushButton("Fit columns")
        fit_button.setToolTip("Fit column widths to their contents")
        fit_button.clicked.connect(self.fit_columns)
        top.addWidget(fit_button)
=======
        self.presets = None
        self._preset_columns = {}
        if kind == "Node":
            self.presets = QComboBox()
            self.presets.setAccessibleName("Node column preset")
            self.presets.setToolTip("Choose which node columns to show")
            self.presets.addItems(["Core", "All"])
            self.presets.currentTextChanged.connect(self._apply_preset)
            top.addWidget(self.presets)
>>>>>>> c08b5e8 (Add plugin-contributed node column presets)
        self.customize_button = None
        if customizable:  # Node and Pod lists: pick which labels become columns
            self.customize_button = QPushButton("Columns…")
            what = "label and annotation" if kind in ANNOTATION_KINDS else "label"
            self.customize_button.setToolTip(f"Show, hide, order and add {what} columns for {kind} lists")
            self.customize_button.clicked.connect(lambda: self.customize_requested.emit(kind))
            top.addWidget(self.customize_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.page_heading = QWidget()
        heading = QHBoxLayout(self.page_heading)
        heading.setContentsMargins(0, 0, 0, 8)
        heading.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        eyebrow = QLabel("CLUSTER RESOURCES")
        eyebrow.setObjectName("pageEyebrow")
        theme.muted(eyebrow)
        titles.addWidget(eyebrow)
        self.page_title = QLabel()
        self.page_title.setObjectName("resourcePageTitle")
        titles.addWidget(self.page_title)
        self.page_subtitle = QLabel()
        theme.muted(self.page_subtitle)
        titles.addWidget(self.page_subtitle)
        heading.addLayout(titles, 1)
        self.page_actions = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_requested.emit)
        self.page_actions.addWidget(refresh_button)
        self.create_button = QPushButton("Create…")
        self.create_button.setObjectName("primaryAction")
        self.create_button.setToolTip(f"Create a new {kind} resource")
        self.create_button.clicked.connect(self.new_requested.emit)
        self.create_button.hide()
        self.page_actions.addWidget(self.create_button)
        heading.addLayout(self.page_actions)
        self.page_heading.hide()
        lay.addWidget(self.page_heading)
        lay.addLayout(top)
        lay.addWidget(self.filter_note)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.refresh_requested.emit)
        self.retry_button.hide()
        status_row.addWidget(self.retry_button)
        self.error_details_button = QPushButton("Error details…")
        self.error_details_button.clicked.connect(self._show_error_details)
        self.error_details_button.hide()
        status_row.addWidget(self.error_details_button)
        status_row.addWidget(self.selection_count)
        lay.addLayout(status_row)
        # Long lists (Nodes, Pods) can take a while the first time: while there is nothing to show yet, a large
        # message takes the place of the empty table. Reloads with rows on screen only use the status line.
        self.loading_page = LoadingPane()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.loading_page)
        lay.addWidget(self.stack, 1)
        self._apply_preset()

    def set_page_title(self, title):
        """Workspace lists have a heading; embedded overview lists stay compact."""
        self.page_title.setText(title)
        self.page_subtitle.setText(f"Browse and inspect {title.lower()} in this cluster.")
        if self.customize_button is not None:
            self._top.removeWidget(self.customize_button)
            self.page_actions.insertWidget(1, self.customize_button)
        self.create_button.setVisible(self.new_button is not None)
        self.page_heading.show()

    def _update_selection_count(self, *_, emit_empty=True):
        count = len(self.view.selectionModel().selectedRows())
        self.selection_count.setText(f"{count} selected")
        if emit_empty and count == 0:
            self.selected.emit(None)

    def set_loading(self):
        self._load_clock.start()
        self.status.setText("Loading…")
        if not self.model.rowCount():
            self.loading_page.show_busy("Loading…")
            self.stack.setCurrentWidget(self.loading_page)
        self._load_timer.start()

    def _show_error_details(self):
        QMessageBox.warning(self, "Refresh failed", self._last_error)

    def _loading_tick(self):
        text = f"Loading… {self._load_clock.elapsed() // 1000}s"
        self.status.setText(text)
        if self.stack.currentWidget() is self.loading_page:
            self.loading_page.show_busy(text)

    def set_error(self, message):
        self._load_timer.stop()
        self._last_error = str(message)
        first = (str(message).splitlines() or [""])[0]
        if self._has_loaded:
            self.status.setText(f"Refresh failed · showing data from {self._loaded_at}: {first}")
            self.retry_button.show()
            self.error_details_button.show()
        else:
            self.status.setText(f"Load failed: {first}")
        if not self._has_loaded:
            actions = [("Retry", self.refresh_requested.emit)]
            if "My permissions" in str(message):
                actions.append(("View permissions", self.permissions_requested.emit))
            self.loading_page.show_failed(f"Load failed: {message}", actions, str(message))
            self.stack.setCurrentWidget(self.loading_page)

    # ---- filter expressions (see filter_expr) ------------------------------------------------------------------
    def _show_filter_completions(self, text):
        """Offer candidates while typing without changing the filter until one is accepted."""
        cursor = self.filter.cursorPosition()
        match = re.search(r"[A-Za-z0-9_.:-]*$", text[:cursor])
        prefix = match.group(0) if match else ""
        if len(prefix) < 2:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount():
            self._completer.complete()

    def _insert_filter_completion(self, completion):
        cursor = self.filter.cursorPosition()
        match = re.search(r"[A-Za-z0-9_.:-]*$", self.filter.text()[:cursor])
        start = match.start() if match else cursor
        self.filter.setSelection(start, cursor - start)
        self.filter.insert(str(completion))

    @staticmethod
    def _manifest_paths(value, prefix, output, depth=0):
        if depth >= 8 or len(output) >= 5000:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
                    continue
                path = f"{prefix}.{key}" if prefix else key
                output.add(path)
                ResourceTable._manifest_paths(child, path, output, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:10]):
                path = f"{prefix}.{index}"
                output.add(path)
                ResourceTable._manifest_paths(child, path, output, depth + 1)

    def _refresh_filter_completions(self):
        candidates = set(self._column_map())
        candidates.update(("kind", "label.", "annotation.", "spec", "status"))
        manifests = 0
        for row in self.model.rows:
            raw = getattr(row, "raw", None)
            if not isinstance(raw, dict):
                continue
            metadata = raw.get("metadata") or {}
            for field in ("labels", "annotations"):
                values = metadata.get(field) or {}
                candidates.update(f"{field[:-1]}.{key}" for key in values if isinstance(key, str))
                if field == "labels":
                    candidates.update(key for key in values if isinstance(key, str))
            for section in ("spec", "status"):
                if section in raw:
                    self._manifest_paths(raw[section], section, candidates)
            manifests += 1
            if manifests >= 200 or len(candidates) >= 5000:
                break
        self._completion_model.setStringList(sorted(candidates, key=str.casefold))

    def _column_map(self):
        """{normalized name: attribute} of the columns: attributes first (`cpu_req`), then headings without their unit (`CPU req (m)`)."""
        columns = self.model.columns
        if self._columns_seen[0] is not columns or len(self._columns_seen[1]) == 0:
            found = {}
            for heading, attr, *_ in columns:
                found.setdefault(filter_expr.normalize(attr), attr)
            for heading, attr, *_ in columns:
                found.setdefault(filter_expr.normalize(heading.split("(")[0]), attr)
            self._columns_seen = (columns, found)
        return self._columns_seen[1]

    @staticmethod
    def _unit_of_attr(attr):
        if attr == "age":
            return "age"
        if attr.endswith("pct"):
            return None
        return "cpu" if attr.startswith("cpu") else "mem" if attr.startswith("mem") else None

    def _unit_of(self, name):
        """For validating an expression: the unit of a column, "" for another column, a label or `kind`, None for nothing known."""
        if name.partition(".")[0] in ("spec", "status") and "." in name:
            return ""  # raw manifest values never inherit a similarly named column's unit
        attr = self._column_map().get(filter_expr.normalize(name))
        if attr is not None:
            return self._unit_of_attr(attr) or ""
        return "" if name.lower() in ("kind",) or name.lower().startswith(("label.", "labels.", "annotation.", "annotations.")) else None

    def _resolve(self, row, name):
        """`(value, unit)` of a column, label or `kind` for one row; None when the name is nothing known; a blank cell has value None."""
        lowered = name.lower()
        if name.partition(".")[0] in ("spec", "status") and "." in name:
            value = getattr(row, "raw", None)
            for part in name.split("."):
                if isinstance(value, dict) and part:
                    value = value.get(part)
                elif isinstance(value, list) and re.fullmatch(r"[0-9]+", part):
                    # Compare before int() so even a very long index cannot raise.
                    index = part.lstrip("0") or "0"
                    if len(index) > len(str(len(value))) or int(index) >= len(value):
                        return None, None
                    value = value[int(index)]
                else:
                    return None, None
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif not isinstance(value, (str, int, float)) or value == "":
                value = None
            return value, None  # negative/zero raw numbers are values, not column sentinels
        for prefix, field in (("label.", "labels"), ("labels.", "labels"), ("annotation.", "annotations"), ("annotations.", "annotations")):
            if lowered.startswith(prefix):
                return (((getattr(row, "raw", None) or {}).get("metadata") or {}).get(field) or {}).get(name[len(prefix):]), None
        attr = self._column_map().get(filter_expr.normalize(name))
        if attr is None:
            labels = ((getattr(row, "raw", None) or {}).get("metadata") or {}).get("labels") or {}
            if name in labels:  # a bare word that is a label key of this row: `app = web`
                return labels[name], None
            return (self.kind, None) if lowered == "kind" else None
        if attr.startswith(("label:", "annotation:")):
            field, _, key = attr.partition(":")
            value = (((row.raw or {}).get("metadata") or {}).get("labels" if field == "label" else "annotations") or {}).get(key)
        elif attr.startswith("plugin:"):
            value = (getattr(row, "extra", None) or {}).get(attr)
        else:
            value = getattr(row, attr, None)
        if attr == "age":
            value = age_sort_value(value)
        if value is None or value == "" or (isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0):
            value = None  # blank: no metrics, no limit, an age that could not be read
        return value, self._unit_of_attr(attr)

    def _apply_filter(self):
        """Plain text matches any column; text with a comparison is a filter expression. A bad expression is explained under the box
        and filters nothing."""
        text = self.filter.text()
        if text.strip() and filter_expr.looks_like_expression(text):
            try:
                expression = filter_expr.parse(text)
                expression.validate(self._unit_of)
            except filter_expr.FilterError as error:
                self.filter_note.setText(f"Filter: {error}  (hover the box for the syntax)")
                self.filter_note.setVisible(True)
                self.proxy.set_expression(None)
                self.proxy.setFilterFixedString("")
                self._update_empty_state()
                return
            self.filter_note.setVisible(False)
            self.proxy.setFilterFixedString("")
            self.proxy.set_expression(expression)
            self._update_empty_state()
            return
        self.filter_note.setVisible(False)
        self.proxy.set_expression(None)
        self.proxy.setFilterFixedString(text)
        self._update_empty_state()

    def _update_empty_state(self):
        if not self._has_loaded or self._last_error:
            return
        if not self.model.rowCount():
            self.loading_page.show_empty("No resources found.", [("Refresh", self.refresh_requested.emit)])
            self.stack.setCurrentWidget(self.loading_page)
        elif self.proxy.rowCount() == 0:
            self.loading_page.show_empty("No resources match this filter.", [("Clear filter", self.filter.clear)])
            self.stack.setCurrentWidget(self.loading_page)
        elif self.stack.currentWidget() is self.loading_page:
            self.stack.setCurrentWidget(self.view)

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
        self.create_button.setVisible(not self.page_heading.isHidden())

    def set_rows(self, rows):
        self._load_timer.stop()
        self._has_loaded = True
        self._last_error = ""
        self.retry_button.hide()
        self.error_details_button.hide()
        self.stack.setCurrentWidget(self.view)
        self.rows_incoming.emit(rows)  # before the columns are measured: a refresh must not blank or shrink plugin columns
        kept = self._selection_to_keep()
        began = time.perf_counter() if timings.enabled else 0.0
        selection = self.view.selectionModel()
        was_blocked = selection.blockSignals(True)
        try:
            self.model.set_rows(rows)
            self._refresh_filter_completions()
            if rows and not self._sized_columns:
                self.fit_columns()
                self._sized_columns = True
            self._restore_selection(*kept)
        finally:
            selection.blockSignals(was_blocked)
        self._update_selection_count(emit_empty=False)
        if timings.enabled:
            timings.record("gui", f"table.{self.kind}", time.perf_counter() - began, rows=len(rows))
        self._loaded_at = time.strftime("%H:%M:%S")
        self.show_status()
        self.rows_set.emit(self.model.rows)
        self._apply_goto(final=True)
        self._update_empty_state()

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
        if self._last_error and self._has_loaded:
            first = (self._last_error.splitlines() or [""])[0]
            self.status.setText(f"Refresh failed · showing data from {self._loaded_at}: {first}")
            return
        count = self.model.rowCount()
        text = f"{count:,} rows" if count else "No resources found"
        metric_rows = [row for row in self.model.rows if hasattr(row, "cpu_used") or hasattr(row, "mem_used")]
        if metric_rows and all(all(getattr(row, field, -1) < 0 for field in ("cpu_used", "mem_used")) for row in metric_rows):
            text += " · Metrics unavailable (metrics-server)"
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
        """Add rows to what is shown (the next page of a paged list). Only the new rows are inserted: the model is not reset, so the
        selection, current row and scroll position stay as they are, the columns keep their widths, and a sort or filter in use
        places just the new rows instead of sorting every row again (a full reset at 18,000 rows froze the window for 10 s)."""
        rows = list(rows)
        if not rows:
            return
        self._load_timer.stop()
        self._has_loaded = True
        self._last_error = ""
        self.retry_button.hide()
        self.error_details_button.hide()
        self.stack.setCurrentWidget(self.view)
        self.rows_incoming.emit(rows)
        began = time.perf_counter() if timings.enabled else 0.0
        self.model.append_rows(rows)
        self._refresh_filter_completions()
        if timings.enabled:
            timings.record("gui", f"table.{self.kind}.append", time.perf_counter() - began, rows=len(rows), total=self.model.rowCount())
        self._loaded_at = time.strftime("%H:%M:%S")
        self.show_status()
        self.rows_set.emit(rows)

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
        selection = self.view.selectionModel()
        was_blocked = selection.blockSignals(True)
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
            selection.blockSignals(was_blocked)
        self.view.verticalScrollBar().setValue(scroll)

    def fit_columns(self):
        """Fit visible columns, limiting long plugin values to a practical width."""
        self.view.resizeColumnsToContents()
        for index, column in enumerate(self.model.columns):
            if column[1].startswith("plugin:") and self.view.columnWidth(index) > 240:
                self.view.setColumnWidth(index, 240)

    def set_plugin_presets(self, presets):
        if self.presets is None:
            return
        current = self.presets.currentText()
        self._preset_columns = {name: set(cols) for name, cols in presets.items()}
        self.presets.blockSignals(True)
        self.presets.clear()
        self.presets.addItems(["Core", "All"] + sorted(name for name in presets if name not in ("Core", "All")))
        index = self.presets.findText(current)
        self.presets.setCurrentIndex(index if index >= 0 else 0)
        self.presets.blockSignals(False)
        self._apply_preset()

    def _apply_preset(self, *_):
        if self.presets is None:
            return
        name = self.presets.currentText()
        core = {"name", "status", "roles", "pods", "cpu", "memory", "age"}
        included = core | self._preset_columns.get("Core", set())
        if name not in ("Core", "All"):
            included |= self._preset_columns.get(name, set())
        for i, column in enumerate(self.model.columns):
            self.view.setColumnHidden(i, name != "All" and column[1] not in included and not column[1].startswith(("label:", "annotation:")))

    def set_extra_columns(self, columns):
        """Columns added by plugins, after the built-in ones. They take part in the saved layout (order, hidden)
        and the Columns… dialog like any built-in column."""
        columns = list(columns)
        if columns == self.extra_columns or self.kind in NOT_CUSTOMIZABLE:
            return
        self.extra_columns = columns
        self.base_columns = self.core_columns + columns
        self.set_label_columns(*(self._label_args or ([],)))
        self._refresh_filter_completions()

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
        self._column_widths.update({col[1]: self.view.columnWidth(i) for i, col in enumerate(self.model.columns)})
        kept = self._selection_to_keep()
        horizontal_scroll = self.view.horizontalScrollBar().value()
        sort_column = header.sortIndicatorSection()
        sort_attr = self.model.columns[sort_column][1] if 0 <= sort_column < len(self.model.columns) else None
        sort_order = header.sortIndicatorOrder()
<<<<<<< HEAD
        selection = self.view.selectionModel()
        was_blocked = selection.blockSignals(True)
        try:
            self.model.set_columns(columns)
            self._refresh_filter_completions()
            new_sort = next((i for i, col in enumerate(columns) if col[1] == sort_attr), -1)
            self.view.sortByColumn(new_sort, sort_order)
            for i, col in enumerate(columns):
                if col[1] in self._column_widths:
                    self.view.setColumnWidth(i, self._column_widths[col[1]])
                else:
                    self.view.resizeColumnToContents(i)
                    if col[1].startswith("plugin:") and self.view.columnWidth(i) > 240:
                        self.view.setColumnWidth(i, 240)
            self._restore_selection(*kept)
            self.view.horizontalScrollBar().setValue(horizontal_scroll)
        finally:
            selection.blockSignals(was_blocked)
        self._update_selection_count(emit_empty=False)
=======
        self.model.set_columns(columns)
        self._refresh_filter_completions()
        new_sort = next((i for i, col in enumerate(columns) if col[1] == sort_attr), -1)
        self.view.sortByColumn(new_sort, sort_order)
        self.view.resizeColumnsToContents()
        self._apply_preset()
>>>>>>> c08b5e8 (Add plugin-contributed node column presets)

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

    def current_row(self):
        current = self.view.currentIndex()
        if not current.isValid():
            return None
        source = self.proxy.mapToSource(current)
        return self.model.rows[source.row()] if source.isValid() else None

    def _bulk_menu(self, menu, rows):
        n = len(rows)
        bulk = lambda action: (lambda: self.bulk_requested.emit(self.kind, action, rows))
        destructive = QMenu(menu)
        def separate(target):
            if not target.isEmpty() and not target.actions()[-1].isSeparator():
                target.addSeparator()
        if n == 2 and all(getattr(r, "raw", None) for r in rows):  # looking only, so it is always offered
            menu.addAction("Compare these two…", lambda: self.compare_requested.emit(self.kind, list(rows)))
        if self.kind in METRIC_KINDS:  # looking only
            separate(menu)
            menu.addAction(f"Usage history of {n} {self.kind}s…", lambda: self.metrics_many_requested.emit(self.kind, list(rows)))
        if self.kind == "Pod":
            separate(menu)
        if self.kind == "Pod":
            menu.addAction(f"Logs of {n} pods…", lambda: self.multi_logs_requested.emit("Pod", list(rows)))
        if self.kind in SCALABLE:
            separate(menu)
            menu.addAction(f"Scale {n} {self.kind}s…", bulk("scale"))
        if self.kind in RESTARTABLE:
            separate(menu)
            menu.addAction(f"Restart {n} {self.kind}s…", bulk("restart"))
        if self.kind == "Node":
            separate(menu)
            menu.addAction(f"Cordon {n} nodes…", bulk("cordon"))
            menu.addAction(f"Uncordon {n} nodes…", bulk("uncordon"))
            menu.addAction(f"Drain {n} nodes…", bulk("drain"))
        if self.kind == "Pod":
            separate(destructive)
            destructive.addAction(action_icon("delete"), f"Terminate {n} pods…", bulk("terminate"))
            destructive.addAction(action_icon("delete"), f"Force terminate {n} pods…", bulk("force-terminate"))
        if self.kind in DELETABLE_KINDS:
            separate(destructive)
            destructive.addAction(action_icon("delete"), f"Delete {n} {self.kind}s…", bulk("delete"))
        if self.kind == "Node":
            separate(destructive)
            destructive.addAction(action_icon("delete"), f"Delete {n} nodes…", bulk("delete-node"))
            destructive.addAction(action_icon("delete"), f"Force delete {n} nodes…", bulk("force-delete-node"))
        if not destructive.isEmpty():
            separate(menu)
            for action in destructive.actions():
                menu.addAction(action)

    NOT_ALLOWED = "  (not allowed for you)"

    def _hint(self, text, action, row, kind=None):
        """`text`, with a note when the rules the server reported say this user may not do `action` here. It is only a note:
        the entry stays clickable (the rules can be incomplete) and the server has the last word."""
        check = self.permission_check
        if check is not None and check(kind or self.kind, action, row) is False:
            return text + self.NOT_ALLOWED
        return text

    def row_actions(self, row):
        """Shared per-object actions for the Details header and the row context menu."""
        actions = []

        def add(key, title, callback, primary=False, dangerous=False, permission=None):
            shown = self._hint(title, permission, row) if permission else title
            actions.append(RowAction(key, shown, callback, primary, dangerous))

        if self.editable:
            add("edit", "Edit…", lambda: self.edit_requested.emit(self.kind, row), primary=self.kind != "Node", permission="edit")
        if self.kind in COPYABLE and self.editable:
            add("copy", "Copy as new…", lambda: self.duplicate_requested.emit(self.kind, row), permission="create")
        if self.kind in SCALABLE:
            add("scale", "Scale…", lambda: self.scale_requested.emit(self.kind, row), primary=True, permission="scale")
        if self.kind in RESTARTABLE:
            add("restart", "Restart…", lambda: self.restart_requested.emit(self.kind, row), primary=True, permission="restart")
        if self.kind in LOG_SOURCES:
            add("multi-logs", "Logs of all pods…", lambda: self.multi_logs_requested.emit(self.kind, row),
                primary=self.kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob"))
        if self.kind == "ServiceAccount":
            add("permissions", "Effective permissions…", lambda: self.rbac_requested.emit(self.kind, row))
        if self.kind in METRIC_KINDS:
            add("usage-history", "Usage history…", lambda: self.metrics_requested.emit(self.kind, row))
        if self.kind == "Pod":
            add("logs", "Logs", lambda: self.action_requested.emit("logs", row), primary=True, permission="logs")
            add("shell", "Connect (shell)…", lambda: self.action_requested.emit("shell", row), permission="shell")
            add("attach", "Attach…", lambda: self.action_requested.emit("attach", row), permission="attach")
            add("debug", "Debug (ephemeral container)…", lambda: self.action_requested.emit("debug", row), permission="debug")
            if ((getattr(row, "raw", {}) or {}).get("spec") or {}).get("ephemeralContainers"):
                add("remove-debug", "Remove debug containers (recreate pod)…",
                    lambda: self.action_requested.emit("remove-debug", row), permission="terminate")
            add("terminate", "Terminate…", lambda: self.action_requested.emit("terminate", row), dangerous=True, permission="terminate")
            add("force-terminate", "Force terminate…", lambda: self.action_requested.emit("force-terminate", row), dangerous=True, permission="terminate")
            add("browse-pod-files", "Browse files…", lambda: self.action_requested.emit("browse-files", row))
            add("copy-files", "Copy files…", lambda: self.copy_files_requested.emit(row))
        if self.kind == "Container":
            add("container-logs", "Logs", lambda: self.action_requested.emit("logs", row), primary=True)
            add("container-shell", "Connect (shell)…", lambda: self.action_requested.emit("shell", row))
            add("container-attach", "Attach…", lambda: self.action_requested.emit("attach", row))
        if self.kind in ("Pod", "Service"):
            add("port-forward", "Port forward…", lambda: self.port_forward_requested.emit(self.kind, row),
                primary=self.kind == "Service")
        if self.kind == "Node":
            add("node-pods", "List pods on this node", lambda: self.action_requested.emit("node-pods", row), primary=True)
            cordoned = ((getattr(row, "raw", {}) or {}).get("spec") or {}).get("unschedulable", False)
            key = "uncordon" if cordoned else "cordon"
            add("cordon", "Uncordon…" if cordoned else "Cordon…",
                lambda a=key: self.action_requested.emit(a, row), primary=True, permission="cordon")
            add("drain", "Drain…", lambda: self.action_requested.emit("drain", row), permission="cordon")
            add("node-shell", "Shell (nsenter)…", lambda: self.action_requested.emit("node-shell", row))
            add("delete-node", "Delete node…", lambda: self.action_requested.emit("delete-node", row), dangerous=True, permission="delete-node")
            add("force-delete-node", "Force delete node…", lambda: self.action_requested.emit("force-delete-node", row),
                dangerous=True, permission="delete-node")
        if self.kind == "CronJob":
            add("cronjob-run", "Run now…", lambda: self.action_requested.emit("cronjob-run", row), primary=True)
            suspended = str(getattr(row, "suspend", "")) == "True"
            key, title = ("cronjob-resume", "Resume…") if suspended else ("cronjob-suspend", "Suspend…")
            add("cronjob-suspend", title, lambda a=key: self.action_requested.emit(a, row), permission="cronjob-suspend")
        if self.kind == "Deployment":
            add("history", "Rollout history / Undo…", lambda: self.history_requested.emit(self.kind, row))
        if self.kind == "PersistentVolumeClaim":
            add("browse-claim-files", "Browse files…", lambda: self.action_requested.emit("browse-files", row))
        if self.kind == "CustomResourceDefinition":
            add("crd-instances", "Browse instances", lambda: self.action_requested.emit("open-crd", row), primary=True)
        if self.kind == "CRDGroup":
            add("crd-group", "Show CRDs of this group", lambda: self.action_requested.emit("open-crd-group", row), primary=True)
        if self.kind == "HelmRelease":
            add("helm-details", "Details / history…", lambda: self.action_requested.emit("helm-details", row), primary=True)
            add("helm-uninstall", "Uninstall…", lambda: self.action_requested.emit("helm-uninstall", row), dangerous=True)
        if self.kind == "Namespace":
            add("namespace-pods", "Show pods", lambda: self.action_requested.emit("open-namespace", row), primary=True)
            add("diagnostics", "Export diagnostics…", lambda: self.action_requested.emit("diagnostics", row))
        if self.kind in DELETABLE_KINDS or self.deletable:
            add("delete", "Delete…", lambda: self.action_requested.emit("delete", row), dangerous=True, permission="delete")
        return actions

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

    def _cell_menu(self, menu, index):
        """Capture the clicked value before a refresh can replace it; filtering uses the same column's underlying value."""
        source = self.proxy.mapToSource(index)
        attr = self.model.columns[source.column()][1]
        value = index.data(Qt.EditRole if attr.startswith("annotation:") else Qt.DisplayRole)
        text = "" if value is None else str(value)
        copy = menu.addAction(action_icon("copy"), "Copy cell value", lambda: QApplication.clipboard().setText(text))
        copy.setEnabled(bool(text))
        name = filter_expr.normalize(attr)
        found = self._resolve(self.model.rows[source.row()], name)
        expression = None
        if self._column_map().get(name) == attr and found is not None and found[0] is not None:
            value_text = str(found[0])
            quote = "'" if "'" not in value_text else '"' if '"' not in value_text else None
            if quote and not any(c in value_text for c in "*?\n\r"):
                expression = f"{name} = {quote}{value_text}{quote}"
            else:
                # Equality treats * and ? as wildcards. Keep exact regex matching for those values
                # and values that cannot fit in a quoted filter string without escaping.
                pattern = re.escape(value_text).replace("'", r"\x27").replace('"', r"\x22")
                expression = f"{name} ~ '\\A{pattern}\\Z'"
        use = menu.addAction(action_icon("search"), "Filter by this value", lambda: self.filter.setText(expression))
        use.setEnabled(expression is not None)
        menu.addSeparator()

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        menu = QMenu(self)
        destructive = QMenu(menu)
        if idx.isValid():
            if not self.view.selectionModel().isRowSelected(idx.row(), idx.parent()):
                self.view.selectRow(idx.row())  # right-click outside the selection acts on that row alone
            rows = self.selected_rows()
            self._cell_menu(menu, idx)
            if self.context_actions is not None:
                self.context_actions(menu, rows)
                if not menu.isEmpty():
                    menu.exec(self.view.viewport().mapToGlobal(pos))
                return
            if len(rows) > 1:
                self._plugin_menu(menu, rows)
                self._bulk_menu(menu, rows)
                if not menu.isEmpty():
                    menu.exec(self.view.viewport().mapToGlobal(pos))
                return
            row = self.model.rows[self.proxy.mapToSource(idx).row()]
            shared = {action.key: action for action in self.row_actions(row)}

            def add_shared(key, target=menu):
                action = shared.get(key)
                if action is not None:
                    item = target.addAction(action.title, action.callback)
                    icon_name = ("edit" if key == "edit" else "copy" if key == "copy" else
                                 "delete" if key in {"delete", "delete-node", "force-delete-node", "terminate",
                                                     "force-terminate", "helm-uninstall", "remove-debug"} else None)
                    if icon_name:
                        item.setIcon(action_icon(icon_name))

            def section():
                if not menu.isEmpty() and not menu.actions()[-1].isSeparator():
                    menu.addSeparator()
            if self.editable:
                add_shared("edit")
            if self.kind in COPYABLE and self.editable:
                add_shared("copy")
            if self.kind == "ServiceAccount" or self.kind in METRIC_KINDS or self.kind in LOG_SOURCES:
                section()
            if self.kind == "ServiceAccount":
                add_shared("permissions")
            if self.kind in METRIC_KINDS:
                add_shared("usage-history")
            if self.kind in LOG_SOURCES:
                add_shared("multi-logs")
            if self.kind in SCALABLE or self.kind in RESTARTABLE or self.kind == "CronJob":
                section()
            if self.kind in SCALABLE:
                add_shared("scale")
            if self.kind in RESTARTABLE:
                add_shared("restart")
            if self.kind == "CronJob":
                add_shared("cronjob-run")
                add_shared("cronjob-suspend")
            if self.kind == "Deployment":
                add_shared("history")
            if self.kind in DELETABLE_KINDS or self.deletable:
                add_shared("delete", destructive)
            if self.kind in ("Pod", "Service"):
                section()
                add_shared("port-forward")
            if self.kind == "Pod":
                add_shared("browse-pod-files")
                add_shared("copy-files")
            if self.kind == "PersistentVolumeClaim":
                section()
                add_shared("browse-claim-files")
            if self.kind == "Node":
                section()
                add_shared("node-pods")
                add_shared("cordon")
                add_shared("drain")
                add_shared("node-shell")
                add_shared("delete-node", destructive)
                add_shared("force-delete-node", destructive)
            if self.kind == "Container":
                section()
                add_shared("container-logs")
                add_shared("container-shell")
                add_shared("container-attach")
            if self.kind == "Pod":
                section()
                add_shared("logs")
                add_shared("shell")
                add_shared("attach")
                add_shared("debug")
                if ((getattr(row, "raw", None) or {}).get("spec") or {}).get("ephemeralContainers"):
                    add_shared("remove-debug")
                add_shared("terminate", destructive)
                add_shared("force-terminate", destructive)
        if idx.isValid():
            self._goto_menu(menu, self.model.rows[self.proxy.mapToSource(idx).row()])
        if self.kind == "CustomResourceDefinition" and idx.isValid():
            add_shared("crd-instances")
        if self.context_actions is not None:
            return
        if self.kind == "HelmRelease" and idx.isValid():
            add_shared("helm-details")
            add_shared("helm-uninstall", destructive)
        if self.kind == "CRDGroup" and idx.isValid():
            add_shared("crd-group")
        if self.kind == "Namespace":
            if idx.isValid():
                add_shared("namespace-pods")
                add_shared("diagnostics")
            menu.addAction(action_icon("create"), "New namespace…", self.new_requested.emit)
        if self.new_button is not None:
            menu.addAction(action_icon("create"), self._hint(f"New {self.kind}…", "create", None), self.new_requested.emit)
        if self.refresh_mode is not None:
            if not menu.isEmpty():
                menu.addSeparator()
            menu.addAction(action_icon("refresh"), "Refresh", self.refresh_requested.emit)
            auto = menu.addAction("Refresh automatically")
            auto.setCheckable(True)
            auto.setChecked(not self.refresh_mode())
            auto.setToolTip("Off: this list reloads only when you press Refresh")
            auto.triggered.connect(lambda checked: self.refresh_mode_requested.emit(bool(checked)))
        if idx.isValid():  # plugin entries come last, after the built-in ones
            self._plugin_menu(menu, [self.model.rows[self.proxy.mapToSource(idx).row()]])
        if not destructive.isEmpty():
            if not menu.isEmpty() and not menu.actions()[-1].isSeparator():
                menu.addSeparator()
            for action in destructive.actions():
                menu.addAction(action)
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
        else:
            rows = self.selected_rows()
            self.selected.emit(rows[0] if rows else None)

    DOUBLE_CLICK = {"Pod": "containers", "Container": "logs", "Node": "node-pods", "Namespace": "open-namespace",
                    "CustomResourceDefinition": "open-crd", "CRDGroup": "open-crd-group",
                    "HelmRelease": "helm-details"}

    def _on_double_click(self, index):
        action = self.DOUBLE_CLICK.get(self.kind)
        if action and index.isValid():
            self.action_requested.emit(action, self.model.rows[self.proxy.mapToSource(index).row()])
