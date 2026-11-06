import re
import subprocess
import sys
from time import monotonic, perf_counter

from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QSignalBlocker, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDockWidget, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QTabWidget, QToolBar)

from ..k8s.api_index import ApiIndex
from ..k8s.permissions import Permissions
from ..k8s.helm import HelmError, helm_uninstall_args
from ..k8s.resources import (CRD_GROUP_COLUMNS, DELETABLE_KINDS, HELM_COLUMNS, SPEC_BY_KEY, SPEC_BY_KIND, SPECS, crd_groups,
                             custom_columns)
from ..plugins.host import PluginHost
from ..workers import Worker
from .account_directory import AccountDirectory, ConfigMapDirectory, IngressClassDirectory, LabelDirectory, SecretDirectory, ServiceDirectory
from .api_index_loader import ApiIndexLoader
from .apply_dialog import ApplyDialog
from .binding_editor import BINDING_KINDS, BindingEditDialog, care_warnings
from .command_view import CommandView
from .customizations import LabelSettings
from .helm_dialog import HelmReleaseDialog
from .detail_panel import DetailPanel
from .edit_dialog import EditDialog
from .log_view import LogView
from .namespace_dialog import NamespaceDialog
from .node_bar import NodeActionBar
from .node_dialogs import DrainDialog, NodeShellDialog
from .permissions_dialog import PermissionsDialog
from .port_forward import PortForwardDialog, PortForwardPanel, PortForwards, forward_ports
from .pod_view import CONTAINER_COLUMNS, PodActionBar, container_rows, pod_containers
from .plugin_actions import PluginActionRunner
from .overview import CLUSTER_CARDS, POD_COLUMNS, WORKLOAD_CARDS, OverviewView, metric_format
from .refresh_policy import RefreshSettings, is_manual
from .resource_table import ResourceTable
from .rollout_dialog import RolloutDialog
from .role_editor import RULE_KINDS, RoleEditDialog, aggregated_note, confirm_risky
from .service_account_editor import ServiceAccountEditDialog
from .data_editor import DATA_KINDS, DataEditDialog, new_manifest
from .network_editor import NetworkEditDialog, new_manifest as network_manifest
from .network_model import NETWORK_KINDS
from .workload_editor import WorkloadEditDialog
from .workload_model import CREATABLE_KINDS, EDIT_KINDS, skeleton
from .scale_monitor import ScaleMonitor
from .terminal import kubectl_args, node_shell_args, open_terminal
from . import theme
from .terminal_settings import TerminalSettings
from .watcher import ChangeWatcher

ALL_NS = "All namespaces"
DEFAULT_NS = "default"  # what a newly opened cluster is scoped to
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
    crd_groups_found = Signal(list)  # [(view key, label, tooltip)]: the navigator lists them under Custom Resources > Groups
    karpenter_found = Signal(list)  # [(view key, label)] when the cluster runs Karpenter; the navigator shows a group
    customize_requested = Signal(str)  # a table's Columns… button was clicked (kind: Node or Pod)
    read_only_changed = Signal(bool)

    def __init__(self, service, context, parent=None, label_settings=None, terminal_settings=None, plugins=None):
        super().__init__(parent)
        self.service = service
        self.context = context
        self.label_settings = label_settings if label_settings is not None else LabelSettings(parent=self)
        self.terminal_settings = terminal_settings if terminal_settings is not None else TerminalSettings(parent=self)
        self.refresh_settings = RefreshSettings(self.label_settings.store)  # which lists reload only on request
        self.pool = QThreadPool.globalInstance()
        self._workers = set()
        self._loads = {}  # view -> latest requested namespace
        self._load_serial = {}  # view -> number of the newest load; results of older (abandoned) loads are dropped
        self._loading = set()
        self._pending_loads = set()
        self._closed = False
        self.read_only = False  # read-only mode: every change is refused (see set_read_only)
        self.helm_runner = subprocess.run  # how `helm uninstall` is run (tests replace it)
        self.port_forwards = PortForwards(parent=self)  # the kubectl port-forward processes of this cluster
        self.permissions = None  # the user's rules in the namespace in front (k8s.permissions.Permissions), or None when unknown
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
        self._clock = perf_counter  # tests replace it
        self._last_load = {}  # view key -> when its last load started; watch reloads wait for the refresh interval
        self._watch_late = QTimer(self, singleShot=True)
        self._watch_late.timeout.connect(self._watch_reload)
        self.setWindowFlags(Qt.Widget)
        # side docks span the full height; the log dock sits under the central tabs only
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        # Plugins (see klotse.plugins): extra columns on the tables, filled in off the GUI thread. None = no plugins.
        self.plugins = plugins
        self.plugin_host = PluginHost(plugins, context, self._run, parent=self) if plugins is not None else None
        self.plugin_runner = PluginActionRunner(self) if plugins is not None else None
        self._build_views()
        self.apply_label_columns()
        for table in self.resource_tables():
            table.permission_check = self.permission_for
            self._wire_refresh(table)
        self.label_settings.changed.connect(self.apply_label_columns)
        if self.plugin_host is not None:
            for table in self.resource_tables():
                self._hook_table(table)
            plugins.settings_changed.connect(self.plugin_host.forget)  # new settings: what was fetched may be wrong now
            self.plugin_host.overview_updated.connect(self._apply_plugin_overview)
            plugins.changed.connect(self._plugins_changed)
            self.plugin_host.updated.connect(self._plugins_updated)

        # central: closable tabs, one per opened view (the widgets persist when a tab is closed)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        # right: object details
        self.detail = DetailPanel()
        self.detail.events_requested.connect(self._load_events)
        self.detail.related_requested.connect(self._load_related)
        self.detail.goto_object.connect(self.go_to_object)
        self.detail_dock = QDockWidget("Details", self)
        self.detail_dock.setWidget(self.detail)
        self.addDockWidget(Qt.RightDockWidgetArea, self.detail_dock)
        self.detail_dock.dockLocationChanged.connect(self._limit_dock)
        self.detail_dock.topLevelChanged.connect(self._limit_dock)

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
                widget.pods_table.goto_requested.connect(self.go_to)
                widget.pods_table.bulk_requested.connect(self.bulk_action)
            if isinstance(widget, ResourceTable):
                if widget.kind in (*CREATABLE_KINDS, *DATA_KINDS, *NETWORK_KINDS):  # these kinds can be created from their list
                    widget.enable_new()
                    widget.new_requested.connect(lambda *_a, k=widget.kind: self.create_resource(k))
                widget.customize_requested.connect(self.customize_requested)
                widget.bulk_requested.connect(self.bulk_action)
                widget.edit_requested.connect(self.edit_resource)
                widget.scale_requested.connect(self.scale_resource)
                widget.restart_requested.connect(self.restart_resource)
                widget.history_requested.connect(self.show_rollout_history)
                widget.goto_requested.connect(self.go_to)
                widget.port_forward_requested.connect(self.start_port_forward)
                widget.action_requested.connect({"Node": self.node_action, "Namespace": self._namespace_action,
                                                 "CustomResourceDefinition": self._crd_action, "CRDGroup": self._crd_action,
                                                 "HelmRelease": self._helm_action,
                                                 **{k: (lambda _action, row, k=k: self.delete_resource(k, row))
                                                    for k in DELETABLE_KINDS}}.get(widget.kind, self.pod_action))
        self.ns_table.new_requested.connect(self.new_namespace)
        self.node_table.visible_changed.connect(self._update_node_pods)

        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        self.ns_combo = QComboBox()
        self.ns_combo.addItems([ALL_NS, DEFAULT_NS])
        self.ns_combo.setCurrentIndex(1)  # start in the "default" namespace; _set_namespaces falls back if it does not exist
        # Searchable like the label picker: type any part of a name to filter the list. Only the chosen item counts
        # (see namespace()), so a half-typed name never scopes a load.
        self.ns_combo.setEditable(True)
        self.ns_combo.setInsertPolicy(QComboBox.NoInsert)
        self.ns_combo.setMinimumContentsLength(18)
        self.ns_combo.setToolTip("Type to search the namespaces")
        completer = QCompleter(self.ns_combo.model(), self.ns_combo)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.ns_combo.setCompleter(completer)
        self.ns_combo.lineEdit().editingFinished.connect(self._reset_ns_text)
        # the selector only applies to namespaced lists, so it is hidden on cluster-wide views (see _sync_ns_selector)
        self._ns_actions = [bar.addWidget(QLabel(" Namespace: ")), bar.addWidget(self.ns_combo)]
        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh)
        bar.addAction(refresh)
        bar.addSeparator()
        # One control, one place: "Auto-refresh: Off / 5 s / 10 s / 30 s / 60 s". `auto_action` is the on/off state behind it (not on the
        # toolbar), so the code that asks "is auto-refresh on?" and the combo can never disagree.
        self.auto_action = QAction("Auto-refresh", self, checkable=True, checked=True)
        self.auto_action.toggled.connect(self.set_auto_refresh)
        self._auto_ms = AUTO_REFRESH_MS  # the interval to come back to when it is switched on again
        self.auto_interval = QComboBox()
        self.auto_interval.addItem("Off", 0)
        for label, ms in AUTO_REFRESH_CHOICES:
            self.auto_interval.addItem(label.replace("s", " s"), ms)
        self.auto_interval.setCurrentIndex(self.auto_interval.findData(AUTO_REFRESH_MS))
        self.auto_interval.setToolTip("How often the lists that update themselves reload (workloads and nodes by default; right-click "
                                      "any list to change that for it). Off stops every automatic reload: use Refresh.")
        self.auto_interval.currentIndexChanged.connect(self._auto_choice_changed)
        self.auto_action.toggled.connect(self._sync_auto_combo)
        bar.addWidget(QLabel(" Auto-refresh: "))
        bar.addWidget(self.auto_interval)
        self.read_only_label = QLabel(" 🔒 Read-only: changes are blocked ")  # symbol and words, never colour alone
        self.read_only_label.setStyleSheet(theme.warning_style())
        self.read_only_label.setToolTip("View > Read-only mode turns it off")
        self._read_only_action = bar.addWidget(self.read_only_label)
        self._read_only_action.setVisible(False)
        self.ns_combo.currentIndexChanged.connect(lambda _=None: self._load_permissions() if self._ready else None)
        self.ns_combo.activated.connect(lambda _=None: self.load_scoped())
        self._sync_ns_selector()

    def _auto_choice_changed(self, _index=None):
        """The user picked Off or an interval in the toolbar."""
        ms = self.auto_interval.currentData()
        if ms:
            self._auto_ms = ms
            self._auto_timer.setInterval(ms)  # before switching on, so the timer starts with the chosen interval
        self.auto_action.setChecked(bool(ms))

    def _sync_auto_combo(self, on):
        """The on/off state changed (the combo, or code): make the combo say the same."""
        wanted = self.auto_interval.findData(self._auto_ms if on else 0)
        if wanted >= 0 and wanted != self.auto_interval.currentIndex():
            blocker = QSignalBlocker(self.auto_interval)
            self.auto_interval.setCurrentIndex(wanted)
            del blocker

    def createPopupMenu(self):
        """The right-click menu of the toolbar and dock title bars: only the docks (so a closed Details or Logs pane can be
        brought back). It must not offer the toolbar itself, which would let the Refresh and Auto-refresh controls be hidden."""
        menu = QMenu(self)
        for dock in (self.detail_dock, self.log_dock):
            menu.addAction(dock.toggleViewAction())
        return menu

    @property
    def kubeconfig(self):
        """Custom kubeconfig path this cluster was opened with (None = default); kubectl commands must use it too."""
        return getattr(self.service, "kubeconfig", None)

    def start(self):
        """Connect; called by the shell after it has hooked up the signals."""
        self.state.emit("connecting")
        self.message.emit(f"Connecting to {self.context}…")
        self._run(self.service.connect, lambda _: self._connected(), self.context, on_error=self._connect_failed)

    def _build_views(self):
        T = ResourceTable
        self.ns_table = T([("Name", "name"), ("Status", "status"), ("Age", "age"), ("Labels", "labels")], "Namespace")
        self.pod_table = T(POD_COLUMNS, "Pod", customizable=True)
        self.node_table = T([("Name", "name"), ("Status", "status"), ("Roles", "roles"), ("Version", "version"),
                           ("IP", "ip"), ("Pods", "pods", lambda v: str(v) if v >= 0 else "--"), ("CPU (cores)", "cpu", lambda v: f"{v:.1f}"),
                             ("Memory (GiB)", "memory", lambda v: f"{v:.2f}"),
                             ("CPU used", "cpu_used", metric_format(lambda v: f"{v:.2f}")),
                             ("Mem used (GiB)", "mem_used", metric_format(lambda v: f"{v:.2f}")), ("Age", "age")], "Node",
                             customizable=True)
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
            "overview": (lambda ns: svc().overview_cluster(), False, self._overview_loaded),
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
        # Helm releases, from the labels of their storage Secrets (no release data is read for the list)
        helm = T(HELM_COLUMNS, "HelmRelease", editable=False)
        self.views["helm-releases"] = (helm, "Helm releases")
        self.loaders["helm-releases"] = (lambda ns: svc().list_helm_releases(ns), True, helm.set_rows)
        # Custom Resources > Groups: the CRDs' API groups, from the same list as Definitions
        groups = T(CRD_GROUP_COLUMNS, "CRDGroup", editable=False)
        self.views["crd-groups"] = (groups, "CRD groups")
        self.loaders["crd-groups"] = (lambda ns: crd_groups(svc().list_resource("crds", ns)), False, groups.set_rows)
        for key, (widget, _) in self.views.items():
            widget.view_key = key

    def resource_tables(self):
        for widget, _ in self.views.values():
            yield from widget.tables() if isinstance(widget, OverviewView) else [widget]

    def _overview_loaded(self, data):
        self.overview.set_data(data)
        if self.plugin_host is not None:  # plugins' cards (a total cost, say): computed when due, last values kept meanwhile
            self.plugin_host.refresh_overview(lambda: self.service.list_nodes())

    def _apply_plugin_overview(self):
        self.overview.set_plugin_cards(self.plugin_host.overview_cards())
        self.overview.set_plugin_values(self.plugin_host.overview_values())

    def _hook_table(self, table):
        """Let plugins add columns to this table (only the customizable lists, not the Overview's drill-downs) and
        ask them for values whenever rows arrive."""
        if self.plugin_host is None or table.customize_button is None or getattr(table, "_plugin_hooked", False):
            return
        table._plugin_hooked = True
        table.set_extra_columns(self.plugins.columns_for(table.kind))
        table.rows_incoming.connect(lambda rows, t=table: self.plugin_host.apply(t.kind, rows))
        table.rows_set.connect(lambda rows, t=table: self.plugin_host.rows_loaded(t.kind, rows))
        table.plugin_actions = self.plugin_runner.offered  # right-click entries plugins add
        table.plugin_action_requested.connect(
            lambda plugin_id, key, rows, t=table: self.plugin_runner.run(plugin_id, key, rows, t.kind))

    def _hooked_tables(self):
        return [t for t in self.resource_tables() if getattr(t, "_plugin_hooked", False)]

    def _plugins_changed(self):
        """A plugin was enabled, disabled or reconfigured: update the columns and ask for the values."""
        for table in self._hooked_tables():
            table.set_extra_columns(self.plugins.columns_for(table.kind))
            self.plugin_host.rows_loaded(table.kind, table.model.rows)
        self._plugins_updated(None)
        self._apply_plugin_overview()  # cards of a plugin that was disabled go, those of one enabled come with the next load
        if self._ready:  # connected: compute the cards now if due (a settings change makes them due)
            self.plugin_host.refresh_overview(lambda: self.service.list_nodes())

    def _plugins_updated(self, kind):
        for table in self._hooked_tables():
            if kind is None or table.kind == kind:
                self.plugin_host.apply(table.kind, table.model.rows)
                table.model.refresh_plugin_columns()
                table.fit_plugin_columns()

    def apply_label_columns(self):
        for table in self.resource_tables():
            table.set_label_columns(self.label_settings.labels(table.kind), self.label_settings.headers(table.kind),
                                    self.label_settings.layout(table.kind), self.label_settings.annotations(table.kind),
                                    self.label_settings.annotation_headers(table.kind))

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
        if key.startswith("crd-group:") and key not in self.views:  # a navigator sub item: its tab is made on first use
            self.open_crd_group(key.partition(":")[2])
            return
        self._opening = True  # open_view loads the view itself; don't also load from the tab-change signal
        try:
            self._show_view(key)
        finally:
            self._opening = False
        if not (self.is_manual(key) and key in self._last_load and not self._stale_scope(key)):  # a manual list loads on first open
            self.load_view(key)

    def foreground_key(self):
        widget = self.tabs.currentWidget()
        return getattr(widget, "view_key", None)

    def is_manual(self, key):
        """True when the list `key` reloads only on request (see refresh_policy): no timer, no watch, no reload when its tab
        comes to the front; the toolbar Refresh and the list's right-click Refresh still work."""
        return is_manual(key, self.refresh_settings.overrides())

    def _wire_refresh(self, table):
        """Give a list its right-click Refresh and Refresh automatically entries and the manual-refresh note."""
        key = getattr(table, "view_key", None)
        if key is None or key not in self.loaders or table.refresh_mode is not None:
            return
        table.refresh_mode = lambda k=key: self.is_manual(k)
        table.refresh_requested.connect(lambda k=key: self.load_view(k))
        table.refresh_mode_requested.connect(lambda auto, k=key, t=table: self.set_manual_refresh(k, not auto, t))

    def set_manual_refresh(self, key, manual, table=None):
        """Right-click > Refresh automatically on a list: choose whether it reloads by itself, and remember the choice. A save
        error is shown and the list keeps its mode."""
        try:
            self.refresh_settings.set_manual(key, manual)
        except OSError as e:
            QMessageBox.warning(self, "Refresh setting", str(e))
            return False
        if table is not None:
            table.show_status()
        self._sync_watch()  # a list that became automatic gets its watch, a manual one loses it
        if not manual:
            self.load_foreground()
        return True

    def _stale_scope(self, key):
        """True when the list `key` was loaded for another namespace than the one chosen now: what it shows is for a scope the
        user has left, whether or not the list reloads by itself."""
        if key not in self._loads or key not in self.loaders:
            return False
        ns = self.namespace()
        return self._loads[key] != (None if (not self.loaders[key][1] or ns == ALL_NS) else ns)

    def load_foreground(self):
        """Reload only the tab the user is looking at (no-op before connecting, after shutdown or while it loads). A list that
        reloads only on request is left alone, unless it shows another namespace than the one chosen now."""
        key = self.foreground_key()
        if (self._ready and not self._closed and self.auto_action.isChecked()
                and key in self.loaders and key not in self._loading and (not self.is_manual(key) or self._stale_scope(key))):
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
            if key in self.loaders and not self.is_manual(key):
                ns = self.namespace()
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
        self._watch_late.stop()

    def _watch_reload(self):
        key = self.foreground_key()
        if self._watched and self._watched[0] == key and not self._closed:
            # A busy cluster changes all the time: a change reloads the list at once, but never more often than the
            # refresh interval (10 s by default), so the list does not flicker faster than the user asked for.
            wait = self._auto_timer.interval() / 1000 - (self._clock() - self._last_load.get(key, float("-inf")))
            if wait > 0:
                self._watch_late.start(int(wait * 1000) + 1)
                return
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
        elif action == "open-crd-group":
            self.open_crd_group(row.name)

    def _helm_action(self, action, row):
        if action == "helm-details":
            self.show_helm_release(row)
        elif action == "helm-uninstall":
            self.uninstall_helm_release(row)

    def show_helm_release(self, row):
        """Details and history of a Helm release (reads that release's few storage Secrets in the background). Looking is
        allowed on a read-only cluster; values stay hidden in the dialog until the user shows them."""
        self.message.emit(f"{self.context}: reading Helm release {row.name}…")
        self._run(self.service.helm_release, lambda data: self._helm_ready(row, data), row.namespace, row.name)

    def helm_dialog(self, row, data):
        dialog = HelmReleaseDialog(self.context, data, can_change=not self.read_only, parent=self)
        dialog.uninstall_requested.connect(lambda: self.uninstall_helm_release(row, dialog))
        return dialog

    def _helm_ready(self, row, data):
        dialog = self.helm_dialog(row, data)
        dialog.exec()
        dialog.deleteLater()

    def uninstall_helm_release(self, row, dialog=None):
        """Uninstall a release with `helm uninstall` (the helm command must be installed): Yes/No (default No) first, never on a
        read-only cluster. The release's Kubernetes objects are removed by Helm, not by Klotse."""
        if not self._writable("uninstall Helm releases"):
            return
        try:
            argv = helm_uninstall_args(self.context, row.namespace, row.name, self.kubeconfig)
        except HelmError as e:
            self._on_error(str(e))
            return
        if QMessageBox.question(dialog or self, "Uninstall release", f"Cluster: {self.context}\nUninstall Helm release "
                                f"{row.namespace}/{row.name}?\n\nHelm removes every object the release created, and its history "
                                "unless it keeps it. This runs the helm command on this computer.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def run():
            flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window flashing up
            try:
                done = self.helm_runner(argv, capture_output=True, text=True, timeout=300, **flags)
            except FileNotFoundError:
                raise RuntimeError("The helm command was not found. Install Helm and make sure it is on the PATH.") from None
            except subprocess.TimeoutExpired:
                raise RuntimeError("helm uninstall did not finish within 5 minutes.") from None
            if done.returncode != 0:
                lines = (done.stderr or done.stdout or "helm failed").strip().splitlines()
                raise RuntimeError(lines[-1] if lines else "helm failed")
            return (done.stdout or "").strip()

        self.message.emit(f"{self.context}: uninstalling Helm release {row.name}…")
        self._run(run, lambda out: self._helm_uninstalled(row, dialog),
                  on_error=(lambda msg: dialog.show_status(f"✖ {msg}")) if dialog is not None else None)

    def _helm_uninstalled(self, row, dialog):
        self.message.emit(f"{self.context}: uninstalled Helm release {row.name}")
        if dialog is not None:
            dialog.accept()
        self.refresh()

    def _find_crd_groups(self):
        """Ask (in the background, quietly, metadata only) which API groups the CRDs belong to, for the sub items of Custom
        Resources > Groups in the navigator."""
        find = getattr(self.service, "crd_group_names", None)
        if find is not None:
            self._run(find, self._crd_groups_found, on_error=lambda _msg: None)

    def _crd_groups_found(self, groups):
        entries = [(f"crd-group:{group}", group, f"{count} CRD{'s' if count != 1 else ''}") for group, count in (groups or {}).items()]
        if entries:
            self.crd_groups_found.emit(entries)

    def open_crd_group(self, group):
        """Open (or focus) a tab with the CRDs of one API group (double-click on a row of Custom Resources > Groups); from
        there a CRD opens its instances like in Definitions."""
        key = f"crd-group:{group}"
        if key not in self.views:
            table = ResourceTable(SPEC_BY_KEY["crds"].columns, "CustomResourceDefinition", editable=False)
            table.view_key = key
            table.selected.connect(self.detail.show_row)
            table.action_requested.connect(self._crd_action)
            self.views[key] = (table, f"CRDs: {group or '(core)'}")
            self.loaders[key] = (lambda ns: [c for c in self.service.list_resource("crds", ns) if (c.group or "") == group],
                                 False, table.set_rows)
            self._wire_refresh(table)
        self.open_view(key)

    def open_custom_resources(self, crd):
        """Open (or focus) a tab listing the instances of a custom resource definition."""
        self.open_view(self._custom_view(crd.group, crd.version, crd.plural, crd.kind, crd.scope == "Namespaced"))

    def _custom_view(self, group, version, plural, kind, namespaced):
        """Register (once) the read-only list of one custom resource's instances and return its view key; the CRD list and
        the Karpenter group in the navigator share it, so both open the same tab."""
        key = f"cr:{plural}.{group}"
        if key not in self.views:
            special = custom_columns(group, kind)  # e.g. Karpenter's NodePool and NodeClaim
            columns = special or ([("Name", "name")] + ([("Namespace", "namespace")] if namespaced else []) + [("Age", "age")])
            table = ResourceTable(columns, "CustomResource", editable=False)
            table.view_key = key
            table.selected.connect(self.detail.show_row)
            table.goto_requested.connect(self.go_to)
            self.views[key] = (table, f"{kind}")
            if special:
                load = lambda ns: self.service.list_custom_objects(group, version, plural, namespaced, ns, kind)
            else:
                load = lambda ns: self.service.list_custom_objects(group, version, plural, namespaced, ns)
            self.loaders[key] = (load, namespaced, table.set_rows)
            self._wire_refresh(table)
        return key

    @staticmethod
    def plural_label(kind):
        """'NodePool' -> 'NodePools', 'EC2NodeClass' -> 'EC2NodeClasses'."""
        return kind + ("es" if kind.endswith("s") else "s")

    def _find_karpenter(self):
        """Ask (in the background, quietly) whether the cluster runs Karpenter; if it does the navigator gets a Karpenter
        group with one list per kind it serves: NodePools, NodeClaims and the provider's node classes."""
        find = getattr(self.service, "karpenter_resources", None)
        if find is not None:
            self._run(find, self._karpenter_found, on_error=lambda _msg: None)

    def _karpenter_found(self, resources):
        entries = [(self._custom_view(r.group, r.version, r.name, r.kind, r.namespaced), self.plural_label(r.kind))
                   for r in resources or []]
        if entries:
            self.karpenter_found.emit(entries)

    def go_to(self, what, name):
        """A row's right-click 'Go to node ...' / 'Go to namespace ...': take the user there. A node opens that node's own tab
        (its action bar and the pods running on it, with the node in Details), like double-clicking it in the Nodes list; a
        namespace scopes the toolbar to it and opens its pods, like double-clicking it in the Namespaces list."""
        if what == "node":
            row = self._node_row(name)
            self.open_node_pods(name, row)
            if row is not None:
                self.detail.show_row(row)
            else:  # the Nodes list was never loaded: load it, and the tab's action bar fills in when it arrives
                self.load_view("nodes")
        elif what == "namespace":
            if self.set_namespace(name):
                self.open_view("pods")
            else:
                self.message.emit(f"{self.context}: namespace {name} is not in the list (refresh and try again)")

    def _namespace_action(self, action, row):
        if action == "open-namespace":  # double-click / "Show pods": scope the toolbar to it and open its pods
            self.set_namespace(row.name)
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
        ns = self.namespace()
        namespace = None if (not scoped or ns == ALL_NS) else ns
        previous = self._loads.get(key, namespace)
        self._loads[key] = namespace
        if key in self._loading:
            if previous == namespace:  # the same request is already running: coalesce repeated refreshes
                self._pending_loads.add(key)
                return
            # The namespace changed mid-load: abandon the running request (it cannot be cancelled, but its result
            # is ignored, see the serial below) and start over now instead of waiting for it to finish.
            self._pending_loads.discard(key)
        serial = self._load_serial[key] = self._load_serial.get(key, 0) + 1
        self._loading.add(key)
        title = self.views[key][1]
        widget = self.views[key][0]
        table = widget if isinstance(widget, (ResourceTable, OverviewView)) else None  # both show loading and errors
        if table is not None:
            table.set_loading()
        started = perf_counter()
        self._last_load[key] = self._clock()
        self.message.emit(f"{self.context} / {title}: Loading…")

        def finished(data, error=False):
            if self._load_serial.get(key) != serial:  # abandoned by a newer load of this view: ignore it entirely
                return
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
        self.port_forwards.stop_all()  # no kubectl port-forward may outlive the window
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

    def port_forward_dialog(self, kind, row):
        return PortForwardDialog(kind, row.name, row.namespace, forward_ports(kind, row.raw), self)

    def start_port_forward(self, kind, row):
        """Right-click > Port forward… on a pod or service: ask for the ports, start `kubectl port-forward` and show the list.
        It changes nothing in the cluster, so a read-only cluster allows it."""
        dialog = self.port_forward_dialog(kind, row)
        accepted = dialog.exec() == QDialog.Accepted
        local, remote = getattr(dialog, "result_ports", (0, 0))
        dialog.deleteLater()
        if not accepted:
            return None
        forward = self.port_forwards.start(self.context, row.namespace, kind, row.name, remote, local, kubeconfig=self.kubeconfig)
        self.message.emit(f"{self.context}: forwarding {forward.target} port {remote}…")
        self.show_port_forwards()
        return forward

    def show_port_forwards(self):
        """Bring the Port forwards tab of the Logs & activity dock to the front, creating it on first use."""
        for i in range(self.log_tabs.count()):
            if getattr(self.log_tabs.widget(i), "key", None) == PortForwardPanel.key:
                self.log_tabs.setCurrentIndex(i)
                break
        else:
            panel = PortForwardPanel(self.port_forwards)
            self.log_tabs.setCurrentIndex(self.log_tabs.addTab(panel, "Port forwards"))
            self.log_tabs.setTabToolTip(self.log_tabs.count() - 1, "Closing this tab stops every forward")
        self.log_dock.show()

    def show_apply_yaml(self):
        """File > Apply YAML…: paste or open manifests, check them with the server and apply them (server-side apply)."""
        dialog = self.apply_dialog()
        dialog.exec()
        dialog.deleteLater()

    def apply_dialog(self):
        ns = DEFAULT_NS if self.namespace() == ALL_NS else self.namespace()
        dialog = ApplyDialog(self.context, ns, self)
        dialog.set_read_only(self.read_only)

        def run(docs, dry_run):
            self.message.emit(f"{self.context}: {'checking' if dry_run else 'applying'} {len(docs)} object(s)…")
            self._run(self.service.apply_documents, lambda results: dialog.show_results(results, dry_run), docs, ns, dry_run,
                      on_error=dialog.show_error)
        dialog.run_requested.connect(run)
        dialog.applied.connect(self.refresh)
        return dialog

    def permission_for(self, kind, action, row=None):
        """True / False / None (cannot tell) for whether the user may do `action` (k8s.permissions.ACTIONS) on a `kind` row.
        Only False is used, to add a note to a menu entry. The rules are those of one namespace, so a row from another
        namespace (the all-namespaces node-pods tab) is not judged, and with All namespaces selected nothing is."""
        found = self.permissions
        if found is None:
            return None
        namespace = getattr(row, "namespace", "") or ""
        if namespace and namespace != found.namespace:
            return None
        return found.allowed_action(kind, action)

    def _load_permissions(self):
        """Ask (quietly, in the background) what the user may do in the namespace in front, for the hints on menu entries.
        Any failure, a service that cannot say, or All namespaces simply means no hints."""
        namespace = self.namespace()
        fetch = getattr(self.service, "my_permissions", None)
        if fetch is None or namespace == ALL_NS:
            self.permissions = None
            return
        self._run(fetch, lambda data, ns=namespace: self._set_permissions(ns, data), namespace,
                  on_error=lambda _msg, ns=namespace: self._set_permissions(ns, None))

    def _set_permissions(self, namespace, data):
        if namespace == self.namespace():  # the answer for a namespace the user has left is dropped
            self.permissions = Permissions(data, namespace) if data else None

    def show_permissions(self):
        """View > My permissions…: the user's own permissions in the namespace in front, and a Can I? check."""
        ns = DEFAULT_NS if self.namespace() == ALL_NS else self.namespace()
        self.message.emit(f"{self.context}: reading your permissions in {ns}…")
        self._run(self.service.my_permissions, lambda perms: self._permissions_ready(ns, perms), ns)

    def permissions_dialog(self, ns, perms):
        dialog = PermissionsDialog(self.context, ns, perms, self)

        def check(verb, group, resource, name):
            self._run(self.service.can_i, lambda answer: dialog.show_check(*answer), verb, group, resource, ns, name,
                      on_error=lambda msg: dialog.show_check(error=msg))
        dialog.check_requested.connect(check)
        return dialog

    def _permissions_ready(self, ns, perms):
        dialog = self.permissions_dialog(ns, perms)
        dialog.exec()
        dialog.deleteLater()

    def _load_events(self, row):
        """The Details panel's Events view asked for the events of `row`; the answer (or the reason there is none) goes
        into the panel, not into a dialog: a missing permission to read events is not worth interrupting for."""
        meta = row.raw.get("metadata") or {}
        self._run(self.service.list_object_events, lambda events, r=row: self.detail.show_events(r, events),
                  meta.get("namespace") or "", meta.get("uid"), on_error=lambda msg, r=row: self.detail.show_events(r, error=msg))

    def set_read_only(self, on):
        """Read-only mode: the banner shows and every change this window could make is refused with an explanation. The
        service refuses as well (KubeService.read_only), so nothing slips past the buttons."""
        self.read_only = on = bool(on)
        try:
            self.service.read_only = on
        except AttributeError:
            pass
        self.read_only_label.setStyleSheet(theme.warning_style())
        self._read_only_action.setVisible(on)
        self.read_only_changed.emit(on)

    def _writable(self, what):
        """True unless the cluster is read-only; then it says so (once per attempt) and returns False."""
        if not self.read_only:
            return True
        QMessageBox.information(self, "Read-only cluster", f"{self.context} is in read-only mode, so you cannot {what}.\n\n"
                                "View > Read-only mode turns it off.")
        return False

    def _load_related(self, row):
        """The Details panel's Related view asked for the objects around `row`; the answer (or why there is none) goes into the
        panel. Only manifests of other objects are read, never a Secret's data."""
        find = getattr(self.service, "related_objects", None)
        if find is None:
            self.detail.show_related(row, [])
            return
        self._run(find, lambda relations, r=row: self.detail.show_related(r, relations), getattr(row, "table_kind", ""), row.raw,
                  on_error=lambda msg, r=row: self.detail.show_related(r, error=msg))

    LIST_VIEWS = {"Pod": "pods", "Deployment": "deployments", "ReplicaSet": "replicasets", "StatefulSet": "statefulsets"}

    def go_to_object(self, kind, namespace, name):
        """A link in the Related view: take the user to that object. A node opens its own tab and a namespace its pods (as Go to
        does); any other kind opens its list, scoped to the object's namespace unless All namespaces is chosen, with the object
        selected so Details shows it."""
        if kind == "Node":
            self.go_to("node", name)
            return
        if kind == "Namespace":
            self.go_to("namespace", name)
            return
        key = self.LIST_VIEWS.get(kind) or (SPEC_BY_KIND[kind].key if kind in SPEC_BY_KIND else None)
        if key is None or key not in self.views:
            self.message.emit(f"{self.context}: there is no list for {kind} to go to")
            return
        if namespace and self.namespace() not in (ALL_NS, namespace) and not self.set_namespace(namespace):
            self.message.emit(f"{self.context}: namespace {namespace} is not in the list (refresh and try again)")
        self.open_view(key)
        self.views[key][0].select_when_loaded(name, namespace)

    def _on_error(self, msg):
        self.message.emit(f"{self.context}: {msg}")
        QMessageBox.warning(self, "Kubernetes error", f"{self.context}\n\n{msg}")

    # ---- actions -------------------------------------------------------
    def api_loader(self):
        """The cluster's API resources for the role editor's pickers: one index per workspace, loaded once in the
        background and reused by every later edit (the editor's Reload button refreshes it)."""
        if getattr(self, "_api_loader", None) is None:
            self._api_loader = ApiIndexLoader(self.service, self._run, ApiIndex(), parent=self)
        self._api_loader.start()
        return self._api_loader

    def account_directory(self):
        """The cluster's service accounts for the binding editor's picker: loaded once, in the background."""
        if getattr(self, "_accounts", None) is None:
            self._accounts = AccountDirectory(self.service, self._run, parent=self)
        self._accounts.start()
        return self._accounts

    MAX_DOCK_SHARE = 0.5  # the Details dock may take at most this much of the window, so it never covers the lists

    def _limit_dock(self, *_args):
        """Cap the Details dock at half the window (width at the sides, height at the top or bottom); a floating one is free."""
        dock = self.detail_dock
        free = 16777215  # QWIDGETSIZE_MAX
        if dock.isFloating():
            dock.setMaximumSize(free, free)
            return
        area = self.dockWidgetArea(dock)
        if area in (Qt.LeftDockWidgetArea, Qt.RightDockWidgetArea):
            dock.setMaximumSize(max(320, int(self.width() * self.MAX_DOCK_SHARE)), free)
        else:
            dock.setMaximumSize(free, max(200, int(self.height() * self.MAX_DOCK_SHARE)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._limit_dock()

    def secret_directory(self, namespace):
        """The secret names of one namespace for the service account editor's pickers: loaded once per namespace."""
        directories = self.__dict__.setdefault("_secret_directories", {})
        if namespace not in directories:
            directories[namespace] = SecretDirectory(self.service, self._run, namespace, parent=self)
        directories[namespace].start()
        return directories[namespace]

    def ingress_class_directory(self):
        """The cluster's IngressClasses for the Ingress editor: loaded once, in the background."""
        if getattr(self, "_ingress_classes", None) is None:
            self._ingress_classes = IngressClassDirectory(self.service, self._run, parent=self)
        self._ingress_classes.start()
        return self._ingress_classes

    def configmap_directory(self, namespace):
        """The config map names of one namespace for the Environment table: loaded once per namespace."""
        directories = self.__dict__.setdefault("_configmap_directories", {})
        if namespace not in directories:
            directories[namespace] = ConfigMapDirectory(self.service, self._run, namespace, parent=self)
        directories[namespace].start()
        return directories[namespace]

    def service_directory(self, namespace):
        """The service names of one namespace for the Ingress editor: loaded once per namespace."""
        directories = self.__dict__.setdefault("_service_directories", {})
        if namespace not in directories:
            directories[namespace] = ServiceDirectory(self.service, self._run, namespace, parent=self)
        directories[namespace].start()
        return directories[namespace]

    def label_directory(self, namespace):
        """The labels on the pods of one namespace for the selector boxes: loaded once per namespace."""
        directories = self.__dict__.setdefault("_label_directories", {})
        if namespace not in directories:
            directories[namespace] = LabelDirectory(self.service, self._run, namespace, parent=self)
        directories[namespace].start()
        return directories[namespace]

    def _has_form(self, kind, raw):
        """Whether the kind has a form editor for this manifest (an aggregated role's rules are overwritten, so no)."""
        return (kind in RULE_KINDS and not raw.get("aggregationRule")) or kind in BINDING_KINDS \
            or kind == "ServiceAccount" or kind in EDIT_KINDS or kind in DATA_KINDS or kind in NETWORK_KINDS

    def _form_for(self, kind, raw, namespace, creating=False, secret_data=None):
        if kind in RULE_KINDS:
            return RoleEditDialog(kind, raw, self.api_loader(), self)
        if kind in BINDING_KINDS:
            return BindingEditDialog(kind, raw, self.account_directory(), self)
        if kind == "ServiceAccount":
            return ServiceAccountEditDialog(kind, raw, self.secret_directory(namespace), self)
        if kind in NETWORK_KINDS:
            return NetworkEditDialog(kind, raw, self, create=creating, namespaces=self._namespace_names(),
                                     classes=self.ingress_class_directory() if kind == "Ingress" else None,
                                     labels=self.label_directory((raw.get("metadata") or {}).get("namespace") or namespace)
                                     if kind != "Ingress" else None,
                                     services=self.service_directory((raw.get("metadata") or {}).get("namespace") or namespace)
                                     if kind == "Ingress" else None)
        if kind in DATA_KINDS:
            return DataEditDialog(kind, raw, self, secret_data=secret_data, create=creating,
                                  namespaces=self._namespace_names())
        ns = (raw.get("metadata") or {}).get("namespace") or namespace
        return WorkloadEditDialog(kind, raw, self, create=creating, namespaces=self._namespace_names(),
                                  ref_names={"Secret": self.secret_directory(ns), "ConfigMap": self.configmap_directory(ns)})

    def _namespace_names(self):
        return [self.ns_combo.itemText(i) for i in range(self.ns_combo.count()) if self.ns_combo.itemText(i) != ALL_NS]

    def edit_resource(self, kind, row):
        self._edit_flow(kind, row.raw, row.name, row.namespace, secret_data=getattr(row, "secret_data", None))

    def create_resource(self, kind):
        """New Deployment, DaemonSet, StatefulSet, Pod, ConfigMap or Secret: the form (or YAML) starts from a skeleton in the
        chosen namespace."""
        namespace = DEFAULT_NS if self.namespace() == ALL_NS else self.namespace()
        raw = (new_manifest(kind, namespace) if kind in DATA_KINDS else
               network_manifest(kind, namespace) if kind in NETWORK_KINDS else skeleton(kind, namespace))
        self._edit_flow(kind, raw, "", "", creating=True)

    def _edit_flow(self, kind, raw, name, namespace, creating=False, secret_data=None):
        """The form and the YAML editor are two views of one manifest: each hands its current (pending) changes to the
        other, until one of them saves or the user cancels."""
        if not self._writable("create or edit objects"):
            return
        current, from_yaml = raw, False
        while True:
            form = None
            if self._has_form(kind, current):
                try:
                    form = self._form_for(kind, current, namespace or (current.get("metadata") or {}).get("namespace", ""),
                                          creating, secret_data)
                except Exception as error:  # noqa: BLE001 - YAML typed by hand that the form cannot show
                    if not from_yaml:
                        raise
                    QMessageBox.warning(self, "Edit in form", f"The form cannot show this YAML ({error}). "
                                        "Fix it here, or keep editing as YAML.")
            if form is not None:
                accepted = form.exec() == QDialog.Accepted
                form.deleteLater()
                if accepted:
                    self._submit(kind, name, form.result_body, creating, namespace)
                    return
                if not form.wants_yaml:
                    return
                current = getattr(form, "pending", None) or current  # the form's pending changes, shown in YAML
            warnings = [] if creating else care_warnings(kind, current)
            note = "" if creating else "\n\n".join(
                ([aggregated_note(current)] if kind in RULE_KINDS and aggregated_note(current) else []) + warnings)
            title = f"New {kind}" if creating else f"Edit {kind} {name}"
            dlg = EditDialog(title, current, self, note=note, can_form=self._has_form(kind, current))
            if dlg.exec() == EditDialog.Accepted:
                if not confirm_risky(self, kind, name, warnings):
                    return
                self._submit(kind, name, dlg.result_body, creating)
                return
            if not getattr(dlg, "wants_form", False):
                return
            current, from_yaml = dlg.pending_body, True  # the YAML as typed, shown in the form

    def _submit(self, kind, name, body, creating, namespace=""):
        name = name or (body.get("metadata") or {}).get("name", "")
        if kind == "Secret" and not creating:  # the form hands over a patch with only what changed, never the whole Secret
            self.message.emit(f"Saving Secret {name}…")
            self._run(self.service.patch_secret, lambda _: self._saved(kind, name), namespace, name, body)
            return
        if creating:
            self.message.emit(f"Creating {kind} {name}…")
            self._run(self.service.create, lambda _: self._created(kind, name), kind, body)
            return
        self.message.emit(f"Saving {kind} {name}…")
        self._run(self.service.replace, lambda _: self._saved(kind, name), kind, body)

    def _created(self, kind, name):
        self.message.emit(f"Created {kind} {name}")
        self.refresh()

    DELETE_NOTES = {
        "ClusterRole": "Bindings that refer to it stop granting anything; aggregated roles are rebuilt by the controller.",
        "Role": "Bindings that refer to it stop granting anything.",
        "ServiceAccount": "Pods that use it can no longer be created, and its tokens stop working.",
        "RoleBinding": "Its subjects lose the access this binding gave them.",
        "ClusterRoleBinding": "Its subjects lose the access this binding gave them.",
        "HorizontalPodAutoscaler": "The workload keeps its current number of replicas and no longer follows the load.",
        "PodDisruptionBudget": "Drains and evictions are no longer limited for the pods it covered.",
        "ResourceQuota": "The namespace is no longer limited by it.",
        "LimitRange": "New pods no longer get its defaults, and its minimums and maximums stop applying.",
    }

    def delete_resource(self, kind, row):
        """Delete one RBAC object after a Yes/No question (default No); the UID precondition guards against a replacement."""
        if not self._writable("delete objects"):
            return
        where = f" in namespace {row.namespace}" if row.namespace else ""
        extra = care_warnings(kind, row.raw)
        note = "\n\n".join(n for n in [self.DELETE_NOTES.get(kind, ""), *extra] if n)
        if QMessageBox.question(self, f"Delete {kind}", f"Cluster: {self.context}\nDelete {kind} {row.name}{where}?"
                                + (f"\n{note}" if note else ""), QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        uid = (row.raw.get("metadata") or {}).get("uid")
        if not uid:
            self._on_error(f"{kind} UID is missing. Refresh the list before deleting.")
            return
        self.message.emit(f"Deleting {kind} {row.name}…")
        self._run(self.service.delete_resource, lambda _: self._deleted(kind, row.name), kind, row.name, row.namespace, uid)

    def _deleted(self, kind, name):
        self.message.emit(f"Deleted {kind} {name}")
        self.refresh()

    def scale_resource(self, kind, row):
        if not self._writable("scale workloads"):
            return
        current = (row.raw.get("spec") or {}).get("replicas", 1)
        text, ok = QInputDialog.getText(
            self, f"Scale {kind}",
            f"New replicas for {row.name} (now {current}):\n\nEnter a number, or change the current count:\n"
            "*2 double   /2 halve (rounds up)   +2 add two   -2 remove two", text=str(current))
        if not ok:
            return
        try:
            n = parse_scale(text, current)
        except ValueError as e:
            QMessageBox.warning(self, "Scale", str(e))
            return
        self.message.emit(f"Scaling {kind} {row.name} to {n}…")
        self._run(self.service.scale, lambda _: self._scaled(kind, row, current, n), kind, row.name, row.namespace, n)

    RESTART_NOTE = ("Restart replaces the pods one by one with the workload's own update strategy "
                    "(like kubectl rollout restart). Pods are recreated, so connections to them are dropped.")

    def restart_resource(self, kind, row):
        if not self._writable("restart workloads"):
            return
        if QMessageBox.question(self, f"Restart {kind}", f"Cluster: {self.context}\nRestart {kind} {row.namespace}/{row.name}?\n\n"
                                f"{self.RESTART_NOTE}", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.message.emit(f"Restarting {kind} {row.name}…")
        self._run(self.service.restart, lambda _: self._restarted(kind, row.name), kind, row.name, row.namespace)

    def show_rollout_history(self, kind, row):
        """The revisions of a Deployment, with a way back to an earlier one. Looking is always allowed; rolling back is not
        on a read-only cluster."""
        self.message.emit(f"{self.context}: reading the rollout history of {row.name}…")
        self._run(self.service.deployment_history, lambda revisions: self._history_ready(row, revisions), row.namespace, row.name)

    def rollout_dialog(self, row, revisions):
        dialog = RolloutDialog(self.context, f"{row.namespace}/{row.name}", revisions, can_change=not self.read_only, parent=self)
        dialog.rollback_requested.connect(lambda revision: self._rollback(row, revision, dialog))
        return dialog

    def _history_ready(self, row, revisions):
        dialog = self.rollout_dialog(row, revisions)
        dialog.exec()
        dialog.deleteLater()

    def _rollback(self, row, revision, dialog):
        if not self._writable("roll back workloads"):
            return
        images = ", ".join(revision["images"]) or "(no images)"
        if QMessageBox.question(
                dialog, "Roll back", f"Cluster: {self.context}\nRoll back {row.namespace}/{row.name} to revision {revision['revision']}?\n\n"
                f"Images: {images}\n\nThe pod template of that revision is put back, which starts a normal rollout "
                "(pods are replaced one by one) and becomes a new revision.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        dialog.show_status("Rolling back…")

        def done(_):
            self.message.emit(f"Rolled {row.name} back to revision {revision['revision']}")
            dialog.show_status(f"✔ Rolled back to revision {revision['revision']}; the pods are being replaced.")
            self.refresh()
            self._run(self.service.deployment_history, dialog.set_revisions, row.namespace, row.name,
                      on_error=dialog.show_error)
        self._run(self.service.rollback_deployment, done, row.namespace, row.name, revision["name"], on_error=dialog.show_error)

    def _restarted(self, kind, name):
        self.message.emit(f"Restarted {kind} {name}: the pods are being replaced")
        self.refresh()

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
        if not self._writable("change several objects at once"):
            return
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
        elif action == "restart":
            if QMessageBox.question(self, f"Restart {kind}s", f"Cluster: {self.context}\nRestart {n} {kind}s?\n\n{names}\n\n"
                                    f"{self.RESTART_NOTE}", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._bulk(f"Restarting {n} {kind}s", rows, lambda r: self.service.restart(kind, r.name, r.namespace))
        elif action == "delete" and kind in DELETABLE_KINDS:
            missing = [r.name for r in rows if not (r.raw.get("metadata") or {}).get("uid")]
            if missing:
                self._on_error(f"{kind} UID is missing for: " + ", ".join(missing) + ". Refresh the list first.")
                return
            flagged = [r.name for r in rows if care_warnings(kind, r.raw)]
            care = (f"\n\nTake care: {len(flagged)} of them are default (system) objects, feed an aggregated role "
                    f"(admin/edit/view) or grant cluster-admin:\n{self._names([r for r in rows if r.name in flagged])}"
                    ) if flagged else ""
            if QMessageBox.question(self, f"Delete {kind}s", f"Cluster: {self.context}\nDelete {n} {kind}s?\n\n{names}{care}",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self._bulk(f"Deleting {n} {kind}s", rows, lambda r: self.service.delete_resource(
                kind, r.name, r.namespace, r.raw["metadata"]["uid"]))
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
        if action != "node-pods" and not self._writable("change nodes or open a shell on one"):
            return
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
            dialog = DrainDialog(self.context, row.name, self, kubeconfig=self.kubeconfig)
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
            argv = node_shell_args(self.context, row.name, dialog.namespace.text().strip(), dialog.image.text().strip(),
                                   kubeconfig=self.kubeconfig)
            self._launch_terminal(argv, f"Opened node shell: {row.name}")

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
            table = ResourceTable(POD_COLUMNS, "Pod", customizable=True)
            table.customize_requested.connect(self.customize_requested)
            bar = NodeActionBar(node)
            bar.set_row(self._node_row(node) or row, gone=False)
            bar.action_requested.connect(lambda action, n=node: self._node_bar_action(action, n))
            self._node_bars[node] = (bar, row)
            table.set_header_widget(bar)
            table.set_label_columns(self.label_settings.labels("Pod"), self.label_settings.headers("Pod"),
                                    self.label_settings.layout("Pod"))
            self._hook_table(table)
            table.view_key = key
            table.filter.setPlaceholderText(f"Filter pods on {node} (all namespaces)…")
            table.selected.connect(self.detail.show_row)
            table.edit_requested.connect(self.edit_resource)
            table.action_requested.connect(self.pod_action)
            table.permission_check = self.permission_for
            table.port_forward_requested.connect(self.start_port_forward)
            table.goto_requested.connect(self.go_to)
            table.bulk_requested.connect(self.bulk_action)
            self.views[key] = (table, f"Pods: {node} (all namespaces)")
            self.loaders[key] = (lambda _: self.service.list_node_pods(node), False,
                                 lambda rows, t=table, b=bar: (t.set_rows(rows), b.set_pods(rows)))
            self._wire_refresh(table)
        self.open_view(key)

    def pod_action(self, action, row):
        if action == "containers":
            self.open_pod_tab(row)
            return
        if action in ("terminate", "force-terminate", "shell", "attach") and not self._writable(
                "terminate pods or open a shell in them"):
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
        argv = kubectl_args(action, self.context, row.namespace, row.name, container, kubeconfig=self.kubeconfig)
        self._launch_terminal(argv, f"Opened {action}: {row.name}")

    def _launch_terminal(self, argv, message):
        self._run(open_terminal, lambda _: self.message.emit(message), argv,
                  self.terminal_settings.mac_terminal)

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
        elif not self._writable("open a shell in a container"):
            return
        else:
            argv = kubectl_args(action, self.context, row.namespace, row.name, container, kubeconfig=self.kubeconfig)
            self._launch_terminal(argv, f"Opened {action}: {row.name}/{container}")

    def new_namespace(self):
        if not self._writable("create namespaces"):
            return
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
        self._find_karpenter()
        self._find_crd_groups()
        self._load_permissions()

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

    def namespace(self):
        """The namespace chosen in the selector (ALL_NS for all). Not the text being typed."""
        return self.ns_combo.itemText(self.ns_combo.currentIndex()) or ALL_NS

    def set_namespace(self, name):
        index = self.ns_combo.findText(name, Qt.MatchExactly)
        if index >= 0:
            self.ns_combo.setCurrentIndex(index)
            self.ns_combo.setEditText(name)
        return index >= 0

    def _reset_ns_text(self):
        """Typed text that is not a namespace is dropped when editing ends; the chosen one stays."""
        self.ns_combo.setEditText(self.namespace())

    def _set_namespaces(self, rows):
        self.ns_table.set_rows(rows)
        names = [r.name for r in rows]
        previous = self.namespace()
        keep = previous if previous in names else ALL_NS  # default (or a deleted namespace) missing: all namespaces
        self.ns_combo.blockSignals(True)
        self.ns_combo.clear()
        self.ns_combo.addItems([ALL_NS] + names)
        self.set_namespace(keep)
        self.ns_combo.blockSignals(False)
        if keep != previous:
            self.load_scoped()
