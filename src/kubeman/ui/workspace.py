import re
from time import monotonic, perf_counter

from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDockWidget, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QToolBar)

from ..k8s.resources import SPECS
from ..workers import Worker
from .command_view import CommandView
from .customizations import LabelSettings
from .detail_panel import DetailPanel
from .edit_dialog import EditDialog
from .log_view import LogView
from .namespace_dialog import NamespaceDialog
from .node_bar import NodeActionBar
from .node_dialogs import DrainDialog, NodeShellDialog
from .pod_view import CONTAINER_COLUMNS, PodActionBar, container_rows, pod_containers
from .overview import CLUSTER_CARDS, POD_COLUMNS, WORKLOAD_CARDS, OverviewView, metric_format
from .resource_table import ResourceTable
from .scale_monitor import ScaleMonitor
from .terminal import kubectl_args, node_shell_args, open_terminal
from .watcher import ChangeWatcher

ALL_NS = "All namespaces"
MAX_REPLICAS = 10000
NODE_PODS_TTL_S = 20  # a node on screen is not recounted more often than this
NODE_PODS_REFRESH_MS = 60_000  # background recount of every node


def parse_scale(text, current):
    """New replica count from "5" (absolute) or an operation on the current count: *2 /2 +1 -1 (/ rounds up)."""
    match = re.fullmatch(r"\s*([*x×/+-]?)\s*(\d+)\s*", text or "")
    if not match:
        raise ValueError(f"Cannot read “{(text or '').strip()}”. Use a number, or *2, /2, +1, -1.")
    op, value = match[1], int(match[2])
    if op in ("*", "x", "×"):
        new = current * value
    elif op == "/":
        if value == 0:
            raise ValueError("Cannot divide by zero.")
        new = -(-current // value)
    elif op == "+":
        new = current + value
    elif op == "-":
        new = current - value
    else:
        new = value
    return max(0, min(MAX_REPLICAS, new))
AUTO_REFRESH_MS = 10_000  # the foreground list reloads this often (default)
WATCH_DEBOUNCE_MS = 800  # a burst of change events becomes one reload
AUTO_REFRESH_CHOICES = [("5s", 5_000), ("10s", 10_000), ("30s", 30_000), ("60s", 60_000)]
KIND_VIEW = {"Deployment": "deployments", "ReplicaSet": "replicasets", "StatefulSet": "statefulsets"}


class ClusterWorkspace(QMainWindow):
    """Everything for one cluster: its own connection, view tabs, namespace filter, details and log docks.

    Embedded as a tab of the main window, so several clusters can be open side by side.
    """

    message = Signal(str)  # status-bar text for the shell
    state = Signal(str)  # connecting | connected | failed
    failed = Signal(str)  # connection error text; the shell closes this workspace
    view_opened = Signal(str)  # view key now showing, so the navigator can follow

    def __init__(self, service, context, parent=None, label_settings=None):
        super().__init__(parent)
        self.service = service
        self.context = context
        self.label_settings = label_settings if label_settings is not None else LabelSettings(parent=self)
        self.pool = QThreadPool.globalInstance()
        self._workers = set()
        self._loads = {}  # view -> latest requested namespace
        self._loading = set()
        self._pending_loads = set()
        self._closed = False
        self._opened_default = False
        self._ready = False  # set once connected
        self._opening = False
        self._auto_timer = QTimer(self, interval=AUTO_REFRESH_MS)
        self._auto_timer.timeout.connect(self._auto_refresh)
        self._node_pods = {}  # node -> last counted pods, kept so a reload does not blank the column
        self._pod_tabs = {}  # view key -> {"row": PodRow, "bar": PodActionBar, "table": ResourceTable}
        self._node_bars = {}  # node -> (action bar of its "pods on this node" tab, row it was opened from)
        self._node_pods_at = {}  # node -> when it was last counted (monotonic seconds)
        self._node_pods_busy = self._node_pods_all_again = False
        self._node_pods_timer = QTimer(self, interval=NODE_PODS_REFRESH_MS)
        self._node_pods_timer.timeout.connect(self._refresh_all_node_pods)
        self._watchers = []
        self._watched = None  # (view key, namespace) the running watches belong to
        self._watch_timer = QTimer(self, interval=WATCH_DEBOUNCE_MS, singleShot=True)
        self._watch_timer.timeout.connect(self._watch_reload)
        self.setWindowFlags(Qt.Widget)
        # side docks span the full height; the log dock sits under the central tabs only
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        self._build_views()
        self.apply_label_columns()
        self.label_settings.changed.connect(self.apply_label_columns)

        # central: closable tabs, one per opened view (the widgets persist when a tab is closed)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        # right: object details
        self.detail = DetailPanel()
        self.detail_dock = QDockWidget("Details (YAML)", self)
        self.detail_dock.setWidget(self.detail)
        self.addDockWidget(Qt.RightDockWidgetArea, self.detail_dock)

        # bottom: log tabs, hidden until the first logs are opened
        self.log_tabs = QTabWidget()
        self.log_tabs.setTabsClosable(True)
        self.log_tabs.setDocumentMode(True)
        self.log_tabs.tabCloseRequested.connect(self._close_log_tab)
        self.log_dock = QDockWidget("Logs && activity", self)
        self.log_dock.setWidget(self.log_tabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        for widget, _ in self.views.values():
            tables = widget.tables() if isinstance(widget, OverviewView) else [widget]
            for t in tables:
                t.selected.connect(self.detail.show_row)
            if isinstance(widget, OverviewView):  # pending/failed pods listed on an overview behave like the Pods tab
                widget.pods_table.edit_requested.connect(self.edit_resource)
                widget.pods_table.action_requested.connect(self.pod_action)
                widget.pods_table.bulk_requested.connect(self.bulk_action)
            if isinstance(widget, ResourceTable):
                widget.bulk_requested.connect(self.bulk_action)
                widget.edit_requested.connect(self.edit_resource)
                widget.scale_requested.connect(self.scale_resource)
                widget.action_requested.connect({"Node": self.node_action, "Namespace": self._namespace_action,
                                                 "CustomResourceDefinition": self._crd_action}.get(widget.kind, self.pod_action))
        self.ns_table.new_requested.connect(self.new_namespace)
        self.node_table.visible_changed.connect(self._update_node_pods)

        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        self.ns_combo = QComboBox()
        self.ns_combo.addItem(ALL_NS)
        # the selector only applies to namespaced lists, so it is hidden on cluster-wide views (see _sync_ns_selector)
        self._ns_actions = [bar.addWidget(QLabel(" Namespace: ")), bar.addWidget(self.ns_combo)]
        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh)
        bar.addAction(refresh)
        bar.addSeparator()
        self.auto_action = QAction("Auto-refresh", self, checkable=True, checked=True)
        self.auto_action.setToolTip("Reload the visible list periodically and when the cluster reports a change")
        self.auto_action.toggled.connect(self.set_auto_refresh)
        bar.addAction(self.auto_action)
        self.auto_interval = QComboBox()
        for label, ms in AUTO_REFRESH_CHOICES:
            self.auto_interval.addItem(label, ms)
        self.auto_interval.setCurrentIndex(next(i for i, (_, ms) in enumerate(AUTO_REFRESH_CHOICES) if ms == AUTO_REFRESH_MS))
        self.auto_interval.currentIndexChanged.connect(
            lambda _=None: self._auto_timer.setInterval(self.auto_interval.currentData()))
        bar.addWidget(self.auto_interval)
        self.ns_combo.activated.connect(lambda _=None: self.load_scoped())
        self._sync_ns_selector()

    def start(self):
        """Connect; called by the shell after it has hooked up the signals."""
        self.state.emit("connecting")
        self.message.emit(f"Connecting to {self.context}…")
        self._run(self.service.connect, lambda _: self._connected(), self.context, on_error=self._connect_failed)

    def _build_views(self):
        T = ResourceTable
        self.ns_table = T([("Name", "name"), ("Status", "status"), ("Age", "age"), ("Labels", "labels")], "Namespace")
        self.pod_table = T(POD_COLUMNS, "Pod")
        self.node_table = T([("Name", "name"), ("Status", "status"), ("Roles", "roles"), ("Version", "version"),
                           ("IP", "ip"), ("Pods", "pods", metric_format(str)), ("CPU (cores)", "cpu", lambda v: f"{v:.1f}"),
                             ("Memory (GiB)", "memory", lambda v: f"{v:.2f}"),
                             ("CPU used", "cpu_used", metric_format(lambda v: f"{v:.2f}")),
                             ("Mem used (GiB)", "mem_used", metric_format(lambda v: f"{v:.2f}")), ("Age", "age")], "Node")
        self.rs_table = T([("Name", "name"), ("Namespace", "namespace"), ("Desired", "desired"), ("Current", "current"),
                           ("Ready", "ready"), ("Owner", "owner"), ("Age", "age")], "ReplicaSet")
        self.deploy_table = T([("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"),
                               ("Up-to-date", "up_to_date"), ("Available", "available"), ("Age", "age")], "Deployment")
        self.sts_table = T([("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"), ("Age", "age")], "StatefulSet")
        self.overview = OverviewView(CLUSTER_CARDS)
        self.wl_overview = OverviewView(WORKLOAD_CARDS)

        # navigator key -> (widget, tab title)
        self.views = {
            "overview": (self.overview, "Overview"), "workloads": (self.wl_overview, "Workloads"),
            "nodes": (self.node_table, "Nodes"), "namespaces": (self.ns_table, "Namespaces"),
            "pods": (self.pod_table, "Pods"), "deployments": (self.deploy_table, "Deployments"),
            "replicasets": (self.rs_table, "ReplicaSets"), "statefulsets": (self.sts_table, "StatefulSets"),
        }
        # navigator key -> (fetch(namespace) -> data, namespace_scoped, sink(data)); service methods are
        # looked up at call time so a swapped/faked service keeps working
        svc = lambda: self.service  # noqa: E731
        self.loaders = {
            "overview": (lambda ns: svc().overview_cluster(), False, self.overview.set_data),
            "workloads": (lambda ns: svc().overview_workloads(ns), True, self.wl_overview.set_data),
            "nodes": (lambda ns: svc().list_nodes(), False, self._set_nodes),
            "namespaces": (lambda ns: svc().list_namespaces(), False, self._set_namespaces),
            "pods": (lambda ns: svc().list_pods(ns), True, self.pod_table.set_rows),
            "deployments": (lambda ns: svc().list_deployments(ns), True, self.deploy_table.set_rows),
            "replicasets": (lambda ns: svc().list_replicasets(ns), True, self.rs_table.set_rows),
            "statefulsets": (lambda ns: svc().list_statefulsets(ns), True, self.sts_table.set_rows),
        }
        for spec in SPECS:
            table = T(spec.columns, spec.kind, editable=spec.editable)
            self.views[spec.key] = (table, spec.title)
            self.loaders[spec.key] = (lambda ns, k=spec.key: svc().list_resource(k, ns), spec.namespaced, table.set_rows)
        for key, (widget, _) in self.views.items():
            widget.view_key = key

    def resource_tables(self):
        for widget, _ in self.views.values():
            yield from widget.tables() if isinstance(widget, OverviewView) else [widget]

    def apply_label_columns(self):
        for table in self.resource_tables():
            table.set_label_columns(self.label_settings.labels(table.kind), self.label_settings.headers(table.kind))

    # ---- views / tabs -------------------------------------------------
    def _show_view(self, key):
        widget, title = self.views[key]
        idx = self.tabs.indexOf(widget)
        if idx < 0:
            idx = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(idx)
        self.view_opened.emit(key)

    def _sync_ns_selector(self):
        """Show the namespace selector only while the foreground view is scoped by namespace."""
        key = self.foreground_key()
        scoped = key not in self.loaders or self.loaders[key][1]
        for action in getattr(self, "_ns_actions", ()):
            action.setVisible(scoped)

    def _on_tab_changed(self, index):
        if index >= 0:
            self.view_opened.emit(self.tabs.widget(index).view_key)
            self._sync_ns_selector()
            if not self._opening:  # a tab that comes to the front is stale, reload it
                self.load_foreground()
            self._sync_watch()

    def open_view(self, key):
        self._opening = True  # open_view loads the view itself; don't also load from the tab-change signal
        try:
            self._show_view(key)
        finally:
            self._opening = False
        self.load_view(key)

    def foreground_key(self):
        widget = self.tabs.currentWidget()
        return getattr(widget, "view_key", None)

    def foreground_kind(self):
        """Resource kind of the list in front ("Pod", "Node", …), or None for overviews and log tabs."""
        return getattr(self.tabs.currentWidget(), "kind", None)

    def load_foreground(self):
        """Reload only the tab the user is looking at (no-op before connecting, after shutdown, or while it loads)."""
        key = self.foreground_key()
        if (self._ready and not self._closed and self.auto_action.isChecked()
                and key in self.loaders and key not in self._loading):
            self.load_view(key)

    def _showing(self):
        return self.isVisible() and not self.window().isMinimized()

    def _auto_refresh(self):
        # skip while this cluster's tab is hidden behind another one, or the window is minimized
        if self._showing():
            self.load_foreground()
        self._sync_watch()  # also notices a window that was minimized or restored

    def _sync_watch(self):
        """Keep change watches running for the foreground view only (and none while hidden, off or closed)."""
        wanted = None
        if self._ready and not self._closed and self.auto_action.isChecked() and self._showing():
            key = self.foreground_key()
            if key in self.loaders:
                ns = self.ns_combo.currentText()
                wanted = (key, None if (not self.loaders[key][1] or ns == ALL_NS) else ns)
        if wanted == self._watched:
            return
        self._stop_watchers()
        self._watched = wanted
        targets = getattr(self.service, "watch_targets", None)
        if wanted is None or targets is None:
            return
        try:
            found = targets(*wanted)
        except Exception:
            return  # not watchable: the periodic reload covers it
        for fn, args in found:
            watcher = ChangeWatcher(lambda stop, f=fn, a=args: self.service.watch_changes(f, a, stop))
            watcher.changed.connect(self._watch_timer.start)
            self._watchers.append(watcher)
            watcher.start()

    def _stop_watchers(self):
        for watcher in self._watchers:
            watcher.stop()
        self._watchers.clear()
        self._watched = None
        self._watch_timer.stop()

    def _watch_reload(self):
        key = self.foreground_key()
        if self._watched and self._watched[0] == key and not self._closed:
            self.load_view(key)  # coalesces with a load that is already running

    def set_auto_refresh(self, enabled):
        """Toolbar toggle: switches the periodic reload (and the change watches) on or off."""
        if enabled and self._ready and not self._closed:
            self._auto_timer.start()
            self._node_pods_timer.start()
            self.load_foreground()
        else:
            self._auto_timer.stop()
            self._node_pods_timer.stop()
        self._sync_watch()

    # ---- pods per node ----------------------------------------------------------
    # Every node is counted once when the list first loads (one pass over all pods). After that, rows are recounted
    # as they come into sight, and everything is recounted every minute while auto-refresh is on.
    def _set_nodes(self, rows):
        for r in rows:
            r.pods = self._node_pods.get(r.name, -1)
        self.node_table.set_rows(rows)
        for name, (bar, _) in self._node_bars.items():  # a real load: a node that is missing has been deleted
            bar.set_row(self._node_row(name))
        if any(r.name not in self._node_pods for r in rows):  # first load, or a node that just joined
            self._count_node_pods(None)

    def _count_node_pods(self, names):
        """Count pods for `names` (one small query each), or for every node when names is None (one pass)."""
        method = getattr(self.service, "all_node_pod_counts" if names is None else "node_pod_counts", None)
        if self._closed or not self._ready or method is None:
            return
        if self._node_pods_busy:
            if names is None:
                self._node_pods_all_again = True  # a full count is still wanted when the current one ends
            return
        self._node_pods_busy = True
        known = [r.name for r in self.node_table.model.rows]
        self._run((method if names is None else (lambda: method(names))),
                  lambda counts: self._node_pods_done(counts, names is None, known),
                  on_error=lambda _msg: self._node_pods_done({}, False, known))

    def _node_pods_done(self, counts, full, known):
        self._node_pods_busy = False
        if full:  # nodes without any pod are absent from the pass, so they count as zero
            counts = {name: counts.get(name, 0) for name in known}
        now = monotonic()
        self._node_pods.update(counts)
        self._node_pods_at.update(dict.fromkeys(counts, now))
        for r in self.node_table.model.rows:
            if r.name in counts:
                r.pods = counts[r.name]
        self.node_table.model.refresh_column("pods")
        if self._node_pods_all_again:
            self._node_pods_all_again = False
            self._count_node_pods(None)

    def _update_node_pods(self):
        """Rows came into sight (scroll, filter, sort, resize): recount the ones not counted lately."""
        if self._closed or not self._ready or not self.node_table.isVisible():
            return
        now = monotonic()
        names = [r.name for r in self.node_table.visible_rows()
                 if now - self._node_pods_at.get(r.name, float("-inf")) > NODE_PODS_TTL_S]
        if names:
            self._count_node_pods(names)

    def _refresh_all_node_pods(self):
        """Minute tick: recount every node in the background (auto-refresh on, cluster showing, nodes loaded)."""
        if self.auto_action.isChecked() and self._showing() and self.node_table.model.rows:
            self._count_node_pods(None)

    def _crd_action(self, action, row):
        if action == "open-crd":
            self.open_custom_resources(row)

    def open_custom_resources(self, crd):
        """Open (or focus) a tab listing the instances of a custom resource definition."""
        key = f"cr:{crd.plural}.{crd.group}"
        namespaced = crd.scope == "Namespaced"
        if key not in self.views:
            columns = [("Name", "name")] + ([("Namespace", "namespace")] if namespaced else []) + [("Age", "age")]
            table = ResourceTable(columns, "CustomResource", editable=False)
            table.view_key = key
            table.selected.connect(self.detail.show_row)
            self.views[key] = (table, f"{crd.kind}")
            self.loaders[key] = (lambda ns: self.service.list_custom_objects(crd.group, crd.version, crd.plural, namespaced, ns),
                                 namespaced, table.set_rows)
        self.open_view(key)

    def _namespace_action(self, action, row):
        if action == "open-namespace":  # double-click / "Show pods": scope the toolbar to it and open its pods
            if self.ns_combo.findText(row.name) >= 0:
                self.ns_combo.setCurrentText(row.name)
            self.open_view("pods")

    def showEvent(self, event):
        super().showEvent(event)
        if self._ready:  # this cluster's tab just came to the front
            self.load_foreground()
            self._sync_watch()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_watch()

    def open_keys(self):
        return [self.tabs.widget(i).view_key for i in range(self.tabs.count())]

    def load_view(self, key):
        if self._closed:
            return
        fetch, scoped, sink = self.loaders[key]
        ns = self.ns_combo.currentText()
        namespace = None if (not scoped or ns == ALL_NS) else ns
        self._loads[key] = namespace
        if key in self._loading:
            self._pending_loads.add(key)
            return
        self._loading.add(key)
        title = self.views[key][1]
        widget = self.views[key][0]
        table = widget if isinstance(widget, ResourceTable) else None
        if table is not None:
            table.set_loading()
        started = perf_counter()
        self.message.emit(f"{self.context} / {title}: Loading…")

        def finished(data, error=False):
            self._loading.discard(key)
            if self._closed:
                return
            if self._loads[key] == namespace:
                if error:
                    if table is not None:
                        table.set_error(data)
                    self.message.emit(f"{self.context} / {title}: {data}")
                else:
                    sink(data)
                    count = f"{len(data):,} rows, " if isinstance(data, list) else ""
                    self.message.emit(f"{self.context} / {title}: {count}{perf_counter() - started:.1f}s")
            if key in self._pending_loads:
                self._pending_loads.discard(key)
                self.load_view(key)

        self._run(fetch, finished, namespace, on_error=lambda m: finished(m, error=True))

    def open_logs(self, row, containers, container=None):
        key = (row.namespace, row.name, container) if container else (row.namespace, row.name)
        for i in range(self.log_tabs.count()):
            if getattr(self.log_tabs.widget(i), "key", None) == key:
                self.log_tabs.setCurrentIndex(i)
                break
        else:
            view = LogView(self.service, row.namespace, row.name, containers, initial_container=container)
            view.key = key
            title = f"Logs: {row.name}/{container}" if container else f"Logs: {row.name}"
            self.log_tabs.setCurrentIndex(self.log_tabs.addTab(view, title))
        self.log_dock.show()

    def _close_log_tab(self, index):
        view = self.log_tabs.widget(index)
        self.log_tabs.removeTab(index)
        view.stop()
        view.deleteLater()
        if self.log_tabs.count() == 0:
            self.log_dock.hide()

    def shutdown(self):
        """Stop every log stream; the shell calls this before discarding the workspace."""
        self._closed = True
        self._auto_timer.stop()
        self._node_pods_timer.stop()
        self._stop_watchers()
        self._pending_loads.clear()
        for i in range(self.log_tabs.count()):
            self.log_tabs.widget(i).stop()

    # ---- worker plumbing ----------------------------------------------
    def _run(self, fn, on_result, *args, on_error=None):
        w = Worker(fn, *args)
        w.setAutoDelete(False)  # keep w.signals alive until its queued signal is delivered
        self._workers.add(w)

        def done(cb, *a):
            self._workers.discard(w)
            if not self._closed:  # disconnected while this call was in flight: drop the result
                cb(*a)

        w.signals.result.connect(lambda r: done(on_result, r))
        w.signals.error.connect(lambda m: done(on_error or self._on_error, m))
        self.pool.start(w)

    def _on_error(self, msg):
        self.message.emit(f"{self.context}: {msg}")
        QMessageBox.warning(self, "Kubernetes error", f"{self.context}\n\n{msg}")

    # ---- actions -------------------------------------------------------
    def edit_resource(self, kind, row):
        dlg = EditDialog(f"Edit {kind} {row.name}", row.raw, self)
        if dlg.exec() != EditDialog.Accepted:
            return
        self.message.emit(f"Saving {kind} {row.name}…")
        self._run(self.service.replace, lambda _: self._saved(kind, row.name), kind, dlg.result_body)

    def scale_resource(self, kind, row):
        current = (row.raw.get("spec") or {}).get("replicas", 1)
        n, ok = QInputDialog.getInt(self, f"Scale {kind}", f"Replicas for {row.name}:", current, 0, 10000)
        if not ok:
            return
        self.message.emit(f"Scaling {kind} {row.name} to {n}…")
        self._run(self.service.scale, lambda _: self._scaled(kind, row, current, n), kind, row.name, row.namespace, n)

    # ---- bulk actions (several rows selected) ---------------------------
    @staticmethod
    def _names(rows, limit=10):
        shown = [f"{ns}/{r.name}" if (ns := getattr(r, "namespace", "")) else r.name for r in rows[:limit]]
        if len(rows) > limit:
            shown.append(f"…and {len(rows) - limit} more")
        return "\n".join(shown)

    def bulk_action(self, kind, action, rows):
        """Apply one action to every selected row. Each row is confirmed once as a group and run in one worker;
        a failure on one row does not stop the others."""
        n = len(rows)
        names = self._names(rows)
        if action == "scale":
            olds = {id(r): (r.raw.get("spec") or {}).get("replicas", 1) for r in rows}
            text, ok = QInputDialog.getText(
                self, f"Scale {n} {kind}s",
                f"New replicas for each of the {n} {kind}s:\n{names}\n\n"
                "Enter a number, or change each one relative to its own count:\n"
                "*2 double   /2 halve (rounds up)   +1 add one   -1 remove one", text=str(olds[id(rows[0])]))
            if not ok:
                return
            try:
                news = {id(r): parse_scale(text, olds[id(r)]) for r in rows}
            except ValueError as e:
                QMessageBox.warning(self, "Scale", str(e))
                return
            self._bulk(f"Scaling {n} {kind}s ({text.strip()})", rows,
                       lambda r: self.service.scale(kind, r.name, r.namespace, news[id(r)]),
                       lambda r: self._scaled(kind, r, olds[id(r)], news[id(r)]), refresh=False)
        elif action in ("terminate", "force-terminate"):
            force = action == "force-terminate"
            note = "\nForce uses zero grace period; finalizers still apply." if force else "\nController-managed pods will be recreated."
            if QMessageBox.question(self, "Terminate pods", f"Cluster: {self.context}\n"
                                    f"{'Force delete' if force else 'Delete'} {n} pods?{note}\n\n{names}",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._bulk(f"Terminating {n} pods", rows,
                       lambda r: self.service.delete_pod(r.name, r.namespace, *((True,) if force else ())))
        elif action in ("cordon", "uncordon"):
            verb = action.capitalize()
            if QMessageBox.question(self, f"{verb} nodes", f"Cluster: {self.context}\n{verb} {n} nodes?\n\n{names}",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._bulk(f"{verb} {n} nodes", rows, lambda r: self.service.cordon_node(r.name, action == "cordon"))
        elif action in ("delete-node", "force-delete-node"):
            force = action == "force-delete-node"
            missing = [r.name for r in rows if not (r.raw.get("metadata") or {}).get("uid")]
            if missing:
                self._on_error("Node UID is missing for: " + ", ".join(missing) + ". Refresh the Nodes view first.")
                return
            if QMessageBox.question(self, "Delete nodes",
                f"Cluster: {self.context}\n{'Force delete' if force else 'Delete'} {n} Kubernetes Nodes?\n\n{names}\n\n"
                "Drain them first. This does not terminate the underlying machines; "
                f"running kubelets may register them again.{chr(10) + 'Force uses zero grace period; finalizers still apply.' if force else ''}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._bulk(f"Deleting {n} nodes", rows, lambda r: self.service.delete_node(
                r.name, r.raw["metadata"]["uid"], *((True,) if force else ())))

    def _bulk(self, title, rows, call, on_each=None, refresh=True):
        """Run call(row) for every row in one background worker, then report successes and failures together."""
        self.message.emit(f"{title}…")

        def work():
            failed = []
            for r in rows:
                try:
                    call(r)
                except Exception as e:  # keep going; report every failure at the end
                    failed.append((r, f"{type(e).__name__}: {e}"))
            return failed

        def done(failed):
            bad = {id(r) for r, _ in failed}
            if on_each:
                for r in rows:
                    if id(r) not in bad:
                        on_each(r)
            self.message.emit(f"{self.context}: {title}: {len(rows) - len(failed)} succeeded, {len(failed)} failed")
            if failed:
                QMessageBox.warning(self, "Some actions failed", f"{self.context}\n\n" + "\n".join(
                    f"{getattr(r, 'namespace', '') + '/' if getattr(r, 'namespace', '') else ''}{r.name}: {m}" for r, m in failed[:10]))
            if refresh or failed:
                self.refresh()

        self._run(work, done)

    def _scaled(self, kind, row, old, new):
        self.message.emit(f"Scaling {kind} {row.name}: {old} → {new}")
        self.open_scale_monitor(kind, row, old, new)
        self.refresh()

    def open_scale_monitor(self, kind, row, old, new):
        """Track a scale in the bottom dock; scaling the same workload again restarts its existing monitor."""
        key = ("scale", kind, row.namespace, row.name)
        for i in range(self.log_tabs.count()):
            view = self.log_tabs.widget(i)
            if getattr(view, "key", None) == key:
                view.restart(old, new)
                self.log_tabs.setCurrentIndex(i)
                self.log_dock.show()
                return view
        mon = ScaleMonitor(self._run, self.service, kind, row.name, row.namespace, old, new)
        mon.key = key
        mon.finished.connect(lambda state, text: self._scale_finished(kind, row.name, text))
        mon.polled.connect(lambda: self._reload_scaled(kind))
        self.log_tabs.setCurrentIndex(self.log_tabs.addTab(mon, f"Scale: {row.name}"))
        self.log_dock.show()
        return mon

    def _reload_scaled(self, kind):
        """Keep the workload and pod lists live while a scale is in flight (only tabs that are open)."""
        open_keys = self.open_keys()
        for key in (KIND_VIEW[kind], "pods"):
            if key in open_keys:
                self.load_view(key)

    def _scale_finished(self, kind, name, text):
        self.message.emit(f"{kind} {name}: {text}")
        self.refresh()

    def node_action(self, action, row):
        if action == "node-pods":
            self.open_node_pods(row.name, row)
        elif action in ("cordon", "uncordon"):
            verb = "Cordon" if action == "cordon" else "Uncordon"
            effect = "Prevent new pods from scheduling here." if action == "cordon" else "Allow new pods to schedule here."
            if QMessageBox.question(self, f"{verb} node",
                                    f"Cluster: {self.context}\n{verb} node {row.name}?\n{effect}",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.message.emit(f"{verb} node {row.name}…")
            self._run(self.service.cordon_node, lambda _: self._node_changed(row.name, verb + " completed"),
                      row.name, action == "cordon")
        elif action in ("delete-node", "force-delete-node"):
            force = action == "force-delete-node"
            verb = "Force delete" if force else "Delete"
            warning = "Force uses zero grace period; finalizers still apply.\n" if force else ""
            if QMessageBox.question(self, "Delete node",
                f"Cluster: {self.context}\n{verb} Kubernetes Node {row.name}?\n"
                "Drain the node first. This does not terminate the underlying machine; "
                f"a running kubelet may register it again.\n{warning}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            uid = (row.raw.get("metadata") or {}).get("uid")
            if not uid:
                self._on_error("Node UID is missing. Refresh the Nodes view before deleting.")
                return
            self.message.emit(f"Deleting node {row.name}…")
            args = (row.name, uid, True) if force else (row.name, uid)
            self._run(self.service.delete_node, lambda _: self._node_changed(row.name, "Deleted"), *args)
        elif action == "drain":
            # Focus an already running drain instead of starting a second one.
            for i in range(self.log_tabs.count()):
                existing = self.log_tabs.widget(i)
                if getattr(existing, "key", None) == ("drain", row.name) and not existing._done:
                    self.log_tabs.setCurrentIndex(i)
                    self.log_dock.show()
                    return
            dialog = DrainDialog(self.context, row.name, self)
            if dialog.exec() != QDialog.Accepted:
                return
            view = CommandView(dialog.command())
            view.key = ("drain", row.name)
            view.completed.connect(lambda success: self._node_changed(
                row.name, "Drain completed" if success else "Drain failed or stopped; see activity"))
            self.log_tabs.setCurrentIndex(self.log_tabs.addTab(view, f"Drain: {row.name}"))
            self.log_dock.show()
            self.message.emit(f"Draining node {row.name}…")
            view.start()
        elif action == "node-shell":
            info = (row.raw.get("status") or {}).get("nodeInfo") or {}
            if info.get("operatingSystem") == "windows":
                self._on_error("nsenter requires a Linux node.")
                return
            dialog = NodeShellDialog(self.context, row.name, self)
            if dialog.exec() != QDialog.Accepted:
                return
            argv = node_shell_args(self.context, row.name, dialog.namespace.text().strip(), dialog.image.text().strip())
            self._run(open_terminal, lambda _: self.message.emit(f"Opened node shell: {row.name}"), argv)

    def _node_changed(self, name, message):
        if self._closed:
            return
        self.message.emit(f"{self.context} / {name}: {message}")
        self.refresh()
        if self._node_bars and self.foreground_key() != "nodes":  # keep the node action bars current (cordoned, gone)
            self.load_view("nodes")

    def _node_row(self, name):
        return next((r for r in self.node_table.model.rows if r.name == name), None)

    def _node_bar_action(self, action, node):
        row = self._node_row(node) or self._node_bars[node][1]
        if row is None:
            self._on_error(f"Node {node} is not loaded. Open the Nodes list first.")
            return
        self.node_action(action, row)

    def open_node_pods(self, node, row=None):
        key = f"node-pods:{node}"
        if key in self.views:
            bar = self._node_bars[node][0]
            bar.set_row(self._node_row(node) or row, gone=False)
        else:
            table = ResourceTable(POD_COLUMNS, "Pod")
            bar = NodeActionBar(node)
            bar.set_row(self._node_row(node) or row, gone=False)
            bar.action_requested.connect(lambda action, n=node: self._node_bar_action(action, n))
            self._node_bars[node] = (bar, row)
            table.set_header_widget(bar)
            table.set_label_columns(self.label_settings.labels("Pod"), self.label_settings.headers("Pod"))
            table.view_key = key
            table.filter.setPlaceholderText(f"Filter pods on {node} (all namespaces)…")
            table.selected.connect(self.detail.show_row)
            table.edit_requested.connect(self.edit_resource)
            table.action_requested.connect(self.pod_action)
            table.bulk_requested.connect(self.bulk_action)
            self.views[key] = (table, f"Pods: {node} (all namespaces)")
            self.loaders[key] = (lambda _: self.service.list_node_pods(node), False, table.set_rows)
        self.open_view(key)

    def pod_action(self, action, row):
        if action == "containers":
            self.open_pod_tab(row)
            return
        if action in ("terminate", "force-terminate"):
            force = action == "force-terminate"
            verb = "Force delete" if force else "Delete"
            warning = ("\nForce removes the pod with zero grace period. Its processes may keep running "
                       "until the kubelet catches up; finalizers still apply.") if force else ""
            ok = QMessageBox.question(self, "Terminate pod", f"Cluster: {self.context}\n{verb} pod {row.namespace}/{row.name}? "
                                      f"Controller-managed pods will be recreated.{warning}",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                return
            self.message.emit(f"Terminating {row.name}…")
            args = (row.name, row.namespace, True) if force else (row.name, row.namespace)
            self._run(self.service.delete_pod, lambda _: self._saved("Pod", row.name), *args)
            return
        containers = [c[0] for c in pod_containers(row.raw)]
        if action == "logs":
            self.open_logs(row, containers)
            return
        container = None
        if len(containers) > 1:
            container, ok = QInputDialog.getItem(self, "Container", "Container:", containers, 0, False)
            if not ok:
                return
        argv = kubectl_args(action, self.context, row.namespace, row.name, container)
        try:
            open_terminal(argv)
        except Exception as e:
            self._on_error(f"{type(e).__name__}: {e}")

    def open_pod_tab(self, row):
        """Open (or focus) a tab for one pod: its containers, with Logs / Shell / Attach per container."""
        key = f"pod:{row.namespace}/{row.name}"
        if key in self._pod_tabs:
            self._pod_tabs[key]["row"] = row
            self._set_pod_tab(key, row)
            self.open_view(key)
            return
        table = ResourceTable(CONTAINER_COLUMNS, "Container", editable=False)
        table.view_key = key
        bar = PodActionBar()
        table.set_header_widget(bar)
        table.selected.connect(self.detail.show_row)
        self._pod_tabs[key] = {"row": row, "bar": bar, "table": table}
        table.action_requested.connect(
            lambda action, container, k=key: self._container_action(action, self._pod_tabs[k]["row"], container.name))
        bar.action_requested.connect(lambda action, k=key: self._pod_tab_action(k, action))
        self.views[key] = (table, f"Pod: {row.name}")

        def fetch(_namespace):
            try:
                return self.service.read_pod(row.name, row.namespace)
            except ApiException as e:
                if e.status == 404:
                    return None  # deleted or replaced
                raise

        self.loaders[key] = (fetch, False, lambda fresh, k=key: self._set_pod_tab(k, fresh))
        self._set_pod_tab(key, row)
        self.open_view(key)

    def _set_pod_tab(self, key, row):
        tab = self._pod_tabs[key]
        if row is None:  # the pod is gone: keep the last containers on screen, disable the actions
            tab["bar"].set_gone(tab["row"].name)
            return
        tab["row"] = row
        tab["bar"].set_row(row)
        tab["table"].set_rows(container_rows(row.raw))

    def _pod_tab_action(self, key, action):
        row = self._pod_tabs[key]["row"]
        if action == "edit":
            self.edit_resource("Pod", row)
        else:
            self.pod_action(action, row)

    def _container_action(self, action, row, container):
        if action == "logs":
            self.open_logs(row, [c[0] for c in pod_containers(row.raw)], container)
        else:
            argv = kubectl_args(action, self.context, row.namespace, row.name, container)
            self._run(open_terminal, lambda _: self.message.emit(f"Opened {action}: {row.name}/{container}"), argv)

    def new_namespace(self):
        dlg = NamespaceDialog(self)
        if dlg.exec() != NamespaceDialog.Accepted:
            return
        name = dlg.result_body["metadata"]["name"]
        self.message.emit(f"Creating namespace {name}…")
        self._run(self.service.create_namespace, lambda _: self._saved("Namespace", name), dlg.result_body)

    def _saved(self, kind, name):
        self.message.emit(f"Saved {kind} {name}")  # also used after create
        self.refresh()

    # ---- connection / data ---------------------------------------------
    def _connect_failed(self, msg):
        self.state.emit("failed")
        self.failed.emit(msg)

    def _connected(self):
        self.state.emit("connected")
        self.message.emit(f"Connected to {self.context}")
        if not self._opened_default:
            self._opened_default = True
            self._show_view("overview")  # refresh() below loads it
        self.refresh()
        self._ready = True
        if self.auto_action.isChecked():
            self._auto_timer.start()
            self._node_pods_timer.start()
        self._sync_watch()

    def refresh(self):
        """Reload the foreground tab plus the namespace list that feeds the toolbar filter.
        Background tabs are not loaded; they reload when they come to the front."""
        for key in dict.fromkeys(["namespaces", self.foreground_key()]):
            if key in self.loaders:
                self.load_view(key)

    def load_scoped(self):
        key = self.foreground_key()
        if key in self.loaders and self.loaders[key][1]:
            self.load_view(key)
        self._sync_watch()  # the watch follows the namespace

    def _set_namespaces(self, rows):
        self.ns_table.set_rows(rows)
        names = [r.name for r in rows]
        current = self.ns_combo.currentText()
        self.ns_combo.blockSignals(True)
        self.ns_combo.clear()
        self.ns_combo.addItems([ALL_NS] + names)
        self.ns_combo.setCurrentText(current if current in names else ALL_NS)
        self.ns_combo.blockSignals(False)
