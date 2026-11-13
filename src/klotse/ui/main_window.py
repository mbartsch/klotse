import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDockWidget, QHBoxLayout, QLabel, QInputDialog,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QSizePolicy, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .about import AboutDialog
from .cluster_settings_dialog import ClusterSettingsDialog
from .cluster_groups import ClusterGroups
from .cluster_aliases import ClusterAliases
from .audit_log import AuditLog
from .audit_dialog import AuditDialog
from .cluster_session import ClusterSession
from ..plugins.manager import PluginManager
from .customizations import ANNOTATION_KINDS, LabelColumnsDialog, LabelSettings
from .edit_menu import EditActions
from .log_view import shutdown_all
from .navigator import Navigator
from ..k8s.connect_help import auth_info
from ..k8s.sign_in import sign_in_argv
from ..k8s.timing import timings
from ..workers import Worker
from .terminal import open_terminal
from .metrics_sources import ForwardDefaults
from .metric_query_settings import QueryDefaults
from .preferences import DebugSettings, KubeconfigSettings, PreferencesDialog
from .big_clusters import NOTICE_TEXT, BigClusters
from .multi_cluster import MultiClusterDialog
from .palette import Entry
from .palette_dialog import PaletteDialog
from .action_icons import action_icon
from .tab_scroll import ScrollableTabBar
from .read_only import ReadOnlyClusters
from . import branding, embedded_terminal, theme
from .theme import ThemeSettings
from .terminal_settings import TerminalSettings
from .workspace import ClusterWorkspace


def _shortcut_tooltip(action, text):
    """Keep a shortcut visible in help text as well as beside its menu item."""
    shortcut = action.shortcut().toString(QKeySequence.NativeText)
    action.setToolTip(f"{text} ({shortcut})" if shortcut else text)


class _MultiSource:
    """What the Several clusters window asks the main window: the open clusters, the list kinds they share, and the loader of each."""

    NOT_TABLES = ("overview", "workloads")

    def __init__(self, window):
        self.window = window
        self.names = list(window.workspaces)

    def _first(self):
        return self.window.workspaces[self.names[0]]

    def contexts(self):
        return self.names

    def kinds(self):
        from .resource_table import ResourceTable
        ws = self._first()
        kinds = []
        for key, (widget, title) in ws.views.items():
            if (isinstance(widget, ResourceTable) and key in ws.loaders
                    and (":" not in key or key.startswith("cr:"))
                    and key not in self.NOT_TABLES and key != "crd-groups"):
                kinds.append((key, "CustomResourceDefinition" if key == "crds" else title))
        return sorted(kinds, key=lambda item: item[1].casefold())

    def columns(self, key):
        return list(self._first().views[key][0].base_columns)

    def table_kind(self, key):
        return self._first().views[key][0].kind

    def fetch(self, context, key):
        """The function that reads `key` from `context` (called with a namespace or None on a worker)."""
        ws = self.window.workspaces.get(context)
        if ws is None or key not in ws.loaders:
            def gone(_namespace):
                raise RuntimeError("this cluster is no longer open")
            return gone
        if key == "pods" and ws.big and ws.huge and hasattr(ws.service, "pod_pager"):  # a huge cluster: its first page, and the window's own pager
            return lambda namespace: ws.service.pod_pager(namespace).next()
        return ws.loaders[key][0]


class MainWindow(QMainWindow):
    """Shell: the cluster navigator on the left and one workspace tab per open cluster.

    `service_factory` is called once per cluster so every workspace gets its own connection.
    """

    closing = Signal()

    def __init__(self, service_factory, label_settings=None, plugin_manager=None):
        super().__init__()
        self.factory = service_factory
        self.workspaces = {}  # context name -> ClusterWorkspace
        self._connection_retries = {}  # context -> (timer, attempts started)
        self._connection_prompts = {}  # context -> non-modal failure dialog
        self._detached = {}  # context name -> top-level window containing its workspace
        self._global_auto_paused = False
        self._global_auto_snapshot = None  # context -> (was enabled, interval ms), captured once per global pause
        self.label_settings = label_settings if label_settings is not None else LabelSettings(parent=self)
        self.terminal_settings = TerminalSettings(self.label_settings.store, parent=self)
        if self.terminal_settings.embedded:  # start QtWebEngine a moment after the window is up, not when the first terminal opens
            QTimer.singleShot(1500, embedded_terminal.warm_up)
        self.kubeconfig_settings = KubeconfigSettings(self.label_settings.store, parent=self)
        self.big_clusters = BigClusters(self.label_settings.store)  # clusters read in the lighter big-cluster way
        self.read_only_clusters = ReadOnlyClusters(self.label_settings.store)  # contexts marked read-only, kept across starts
        self.debug_settings = DebugSettings(self.label_settings.store)  # Preferences > Debug: the timing log
        self.apply_timing_log()
        self.audit_log = AuditLog(os.path.join(os.path.dirname(self.label_settings.store.fileName()), "audit.log"))  # what was changed from here
        self.session = ClusterSession(self.label_settings.store)  # clusters to reconnect, only if the user said so
        self.theme_settings = ThemeSettings(self.label_settings.store)
        self.groups = ClusterGroups(self.label_settings.store)  # folders in the cluster list
        self.aliases = ClusterAliases(self.label_settings.store)
        # plugins: bundled, pip (entry points) and the plugins folder next to the settings file; all off until enabled
        self.plugins = plugin_manager if plugin_manager is not None else PluginManager(
            self.label_settings.store, folder=Path(self.label_settings.store.fileName()).parent / "plugins", parent=self)
        self.setWindowTitle("Klotse")
        self.resize(1500, 850)
        if sys.platform == "darwin":
            self.menuBar().setNativeMenuBar(True)
        self._shutting_down = False
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence.Quit)
        _shortcut_tooltip(self.exit_action, "Exit Klotse")
        self.exit_action.setMenuRole(QAction.QuitRole)
        self.exit_action.triggered.connect(self.close)
        file_menu = self.menuBar().addMenu("File")
        self.apply_action = QAction("Apply YAML…", self)
        self.apply_action.setToolTip("Paste or open manifests and apply them to the cluster in front")
        self.apply_action.triggered.connect(self.show_apply_yaml)
        file_menu.addAction(self.apply_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)
        file_menu.aboutToShow.connect(lambda: self.apply_action.setEnabled(self.clusters.currentWidget() is not None))
        self.edit_menu = self.menuBar().addMenu("Edit")
        self.edit_actions = EditActions(self.edit_menu, self)  # Undo / Redo / Cut / Copy / Paste / Select All
        self.edit_menu.addSeparator()
        self.preferences_action = QAction("Preferences…", self)
        # Qt moves PreferencesRole into the macOS application menu (under Klotse);
        # on Windows and Linux it remains in Edit.
        self.preferences_action.setMenuRole(QAction.PreferencesRole)
        self.preferences_action.setShortcut(QKeySequence.Preferences)
        _shortcut_tooltip(self.preferences_action, "Open preferences")
        self.preferences_action.triggered.connect(lambda: QTimer.singleShot(0, self.open_preferences))
        self.edit_menu.addAction(self.preferences_action)
        self.managed_fields_action = QAction("Show managedFields in details", self, checkable=True, checked=False)
        self.managed_fields_action.setToolTip("Include metadata.managedFields in the Details pane (hidden by default)")
        view_menu = self.menuBar().addMenu("View")
        navigate_menu = view_menu.addMenu("Navigate")
        insights_menu = view_menu.addMenu("Insights")
        operations_menu = view_menu.addMenu("Operations")
        self.panels_menu = view_menu.addMenu("Panels")
        self.panels_menu.aboutToShow.connect(self._fill_workspace_panels_menu)
        settings_menu = view_menu.addMenu("Settings")
        # Prevent macOS's text heuristic from promoting View > Settings into
        # the application Preferences menu; that slot belongs to the explicit
        # PreferencesRole action in Edit.
        settings_menu.menuAction().setMenuRole(QAction.NoRole)
        self.permissions_action = QAction("My permissions…", self)
        self.permissions_action.setToolTip("What you may do in the namespace of the cluster in front, and a Can I? check")
        self.permissions_action.triggered.connect(self.show_permissions)
        insights_menu.addAction(self.permissions_action)
        self.search_action = QAction("Search objects…", self)
        self.search_action.setIcon(action_icon("search"))
        self.search_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        _shortcut_tooltip(self.search_action, "Find objects by name, label, kind or namespace across the cluster in front")
        self.search_action.triggered.connect(self.show_search)
        navigate_menu.addAction(self.search_action)
        self.audit_action = QAction("Change log…", self)
        self.audit_action.setToolTip("What was changed from Klotse: when, which cluster, what, and whether the cluster accepted it")
        self.audit_action.triggered.connect(self.show_audit_log)
        operations_menu.addAction(self.audit_action)
        self.selector_logs_action = QAction("Logs by label selector…", self)
        self.selector_logs_action.setToolTip("Follow the logs of every pod that matches a label selector, in one tab")
        self.selector_logs_action.triggered.connect(self.show_selector_logs)
        operations_menu.addAction(self.selector_logs_action)
        operations_menu.aboutToShow.connect(lambda: self.selector_logs_action.setEnabled(self.clusters.currentWidget() is not None))
        self.diagnostics_action = QAction("Export diagnostics…", self)
        self.diagnostics_action.setToolTip("Save the manifests, events and logs of the namespace in front as a zip file (no Secrets)")
        self.diagnostics_action.triggered.connect(self.export_diagnostics)
        operations_menu.addAction(self.diagnostics_action)
        operations_menu.aboutToShow.connect(lambda: self.diagnostics_action.setEnabled(self.clusters.currentWidget() is not None))
        self.rbac_action = QAction("RBAC lookup…", self)
        self.rbac_action.setToolTip("Who can do something, and what a user, group or service account can do")
        self.rbac_action.triggered.connect(self.show_rbac)
        insights_menu.addAction(self.rbac_action)
        insights_menu.aboutToShow.connect(lambda: self.rbac_action.setEnabled(self.clusters.currentWidget() is not None))
        self.metrics_query_action = QAction("Metrics query…", self)
        self.metrics_query_action.setToolTip("Quick PromQL against the Prometheus or VictoriaMetrics source of the cluster in front (read only)")
        self.metrics_query_action.triggered.connect(self.show_cluster_metrics_query)
        insights_menu.addAction(self.metrics_query_action)
        insights_menu.aboutToShow.connect(lambda: self.metrics_query_action.setEnabled(self.clusters.currentWidget() is not None))
        self.port_forwards_action = QAction("Port forwards…", self)
        self.port_forwards_action.setToolTip("The kubectl port-forwards of the cluster in front: state, address, save, stop")
        self.port_forwards_action.triggered.connect(self.show_port_forwards)
        operations_menu.addAction(self.port_forwards_action)
        operations_menu.aboutToShow.connect(lambda: self.port_forwards_action.setEnabled(self.clusters.currentWidget() is not None))
        self.rightsizing_action = QAction("Right-sizing…", self)
        self.rightsizing_action.setToolTip("Requests and limits against current usage, per workload, with advice")
        self.rightsizing_action.triggered.connect(self.show_rightsizing)
        insights_menu.addAction(self.rightsizing_action)
        insights_menu.aboutToShow.connect(lambda: self.rightsizing_action.setEnabled(self.clusters.currentWidget() is not None))
        self.palette_action = QAction("Command palette…", self)
        self.palette_action.setShortcut(QKeySequence("Ctrl+K"))
        _shortcut_tooltip(self.palette_action, "Jump to a view, namespace or cluster, or run an action, from one search box")
        self.palette_action.triggered.connect(self.show_palette)
        navigate_menu.addAction(self.palette_action)
        self.review_action = QAction("Review changes before saving", self, checkable=True)
        self.review_action.setToolTip("Before an edit is stored, ask the server for a dry run and show what would change (nothing is stored by the preview)")
        self.review_action.setChecked(str(self.label_settings.store.value("edit/review_changes", "true")).lower() != "false")
        self.review_action.toggled.connect(self.set_review_changes)
        settings_menu.addAction(self.review_action)
        self.multi_action = QAction("Multi Cluster…", self)
        self.multi_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        _shortcut_tooltip(self.multi_action, "One list of a kind (Pods, Deployments, Nodes, ...) from several open clusters, with a Cluster column")
        self.multi_action.triggered.connect(self.show_multi)
        navigate_menu.addAction(self.multi_action)
        navigate_menu.aboutToShow.connect(lambda: self.multi_action.setEnabled(bool(self.workspaces)))
        self.trivy_action = QAction("Trivy reports…", self)
        self.trivy_action.setToolTip("Vulnerability, config audit, exposed secret and RBAC reports written by the Trivy Operator (read only)")
        self.trivy_action.triggered.connect(self.show_trivy)
        insights_menu.addAction(self.trivy_action)
        insights_menu.aboutToShow.connect(lambda: self.trivy_action.setEnabled(self.clusters.currentWidget() is not None))
        self.security_action = QAction("Pod security…", self)
        self.security_action.setToolTip("Workloads that break the Pod Security Standards: privileged, host access, hostPath, running as root")
        self.security_action.triggered.connect(self.show_security)
        insights_menu.addAction(self.security_action)
        insights_menu.aboutToShow.connect(lambda: self.security_action.setEnabled(self.clusters.currentWidget() is not None))
        self.preflight_action = QAction("Upgrade preflight…", self)
        self.preflight_action.setToolTip("Before an upgrade or a drain: deprecated APIs, PodDisruptionBudgets that allow no disruption, "
                                         "kubelet version skew, certificate dates")
        self.preflight_action.triggered.connect(self.show_preflight)
        insights_menu.addAction(self.preflight_action)
        insights_menu.aboutToShow.connect(lambda: self.preflight_action.setEnabled(self.clusters.currentWidget() is not None))
        self.unused_action = QAction("Unused objects…", self)
        self.unused_action.setToolTip("ConfigMaps, Secrets and claims that no pod, workload, Ingress or Service Account refers to")
        self.unused_action.triggered.connect(self.show_unused)
        insights_menu.addAction(self.unused_action)
        insights_menu.aboutToShow.connect(lambda: self.unused_action.setEnabled(self.clusters.currentWidget() is not None))
        self.health_action = QAction("Health check…", self)
        self.health_action.setToolTip("Problems and risks in the namespace in front: crash loops, no limits or probes, latest tags, unbound claims")
        self.health_action.triggered.connect(self.show_health)
        insights_menu.addAction(self.health_action)
        insights_menu.aboutToShow.connect(lambda: self.health_action.setEnabled(self.clusters.currentWidget() is not None))
        self.capacity_action = QAction("Capacity…", self)
        self.capacity_action.setToolTip("Requests, limits and usage against what each node can give, per node and per namespace")
        self.capacity_action.triggered.connect(self.show_capacity)
        insights_menu.addAction(self.capacity_action)
        insights_menu.aboutToShow.connect(lambda: self.capacity_action.setEnabled(self.clusters.currentWidget() is not None))
        insights_menu.aboutToShow.connect(lambda: self.permissions_action.setEnabled(self.clusters.currentWidget() is not None))
        navigate_menu.aboutToShow.connect(lambda: self.search_action.setEnabled(self.clusters.currentWidget() is not None))
        self.read_only_action = QAction("Read-only mode", self, checkable=True)
        self.read_only_action.setToolTip("Refuse every change to the cluster in front (useful for production); kept for next time")
        self.read_only_action.triggered.connect(self.set_read_only)
        settings_menu.addAction(self.read_only_action)
        settings_menu.aboutToShow.connect(self._sync_read_only_action)
        self.big_action = QAction("Big cluster mode", self, checkable=True)
        self.big_action.setToolTip("Lighter reads for a cluster with many nodes and pods (Warning events only on the Overview, bigger "
                                   "pages, parallel reads, slower automatic reloads). Found automatically; this is your own choice.")
        self.big_action.triggered.connect(self.set_big_mode)
        settings_menu.addAction(self.big_action)
        self.auto_refresh_menu = settings_menu.addMenu("Auto-refresh across clusters")
        self.pause_auto_refresh_all_action = QAction("Pause on all clusters", self, checkable=True)
        self.pause_auto_refresh_all_action.setToolTip("Pause every open cluster and remember its current on/off state and interval")
        self.pause_auto_refresh_all_action.toggled.connect(self._set_global_auto_paused)
        self.auto_refresh_menu.addAction(self.pause_auto_refresh_all_action)
        self.restore_auto_refresh_action = QAction("Restore previous settings", self)
        self.restore_auto_refresh_action.setToolTip("Restore each cluster's saved on/off state and refresh interval")
        self.restore_auto_refresh_action.triggered.connect(self._restore_global_auto_refresh)
        self.auto_refresh_menu.addAction(self.restore_auto_refresh_action)
        self.enable_auto_refresh_all_action = QAction("Enable on all clusters", self)
        self.enable_auto_refresh_all_action.setToolTip("Turn on auto-refresh for every open cluster, keeping its chosen interval")
        self.enable_auto_refresh_all_action.triggered.connect(self._enable_auto_refresh_all)
        self.auto_refresh_menu.addAction(self.enable_auto_refresh_all_action)
        self.auto_refresh_menu.aboutToShow.connect(self._sync_global_auto_actions)
        view_menu.addSeparator()
        view_menu.addAction(self.managed_fields_action)
        settings_menu.aboutToShow.connect(self._sync_big_action)
        # Plugins: shown only while an enabled plugin has settings for each cluster; its entries need a cluster in front
        self.plugins_menu = self.menuBar().addMenu("Plugins")
        self.plugins_menu.aboutToShow.connect(self._fill_plugins_menu)
        self.plugins.changed.connect(self._update_plugins_menu)
        self.about_action = QAction("About Klotse", self)
        self.about_action.setMenuRole(QAction.AboutRole)
        self.about_action.triggered.connect(self.show_about)
        self.menuBar().addMenu("Help").addAction(self.about_action)

        self.clusters = QTabWidget()
        self.clusters.setDocumentMode(True)
        self.clusters.setAccessibleName("Open cluster workspaces")
        self.clusters.setTabBar(ScrollableTabBar())
        self.clusters.setTabBarAutoHide(True)
        self.clusters.setTabsClosable(True)
        self.clusters.tabCloseRequested.connect(self._close_cluster_tab)
        self.clusters.setContextMenuPolicy(Qt.CustomContextMenu)
        self.clusters.customContextMenuRequested.connect(self._cluster_tab_menu)
        self.clusters.currentChanged.connect(self._on_cluster_changed)
        self.clusters.currentChanged.connect(lambda _index: self._sync_cluster_picker())
        self.setCentralWidget(self.clusters)

        self.nav = Navigator()
        nav_dock = QDockWidget(self)
        nav_dock.setObjectName("clustersDock")
        title = QWidget()
        title.setObjectName("navigatorBrand")
        title.setContextMenuPolicy(Qt.PreventContextMenu)
        title.setContextMenuPolicy(Qt.NoContextMenu)
        title_layout = QHBoxLayout(title)
        title_layout.setContentsMargins(16, 14, 12, 14)
        title_layout.setSpacing(10)
        logo = QLabel()
        logo.setObjectName("klotseLogo")
        logo.setContextMenuPolicy(Qt.PreventContextMenu)
        logo.setPixmap(branding.square_logo(28))
        logo.setFixedSize(28, 28)
        title_layout.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_name = QLabel("Klotse")
        brand_name.setContextMenuPolicy(Qt.NoContextMenu)
        brand_font = brand_name.font()
        brand_font.setPointSizeF(11)
        brand_font.setBold(True)
        brand_name.setFont(brand_font)
        brand_text.addWidget(brand_name)
        title_layout.addLayout(brand_text, 1)
        self.cluster_picker = QComboBox()
        self.cluster_picker.setAccessibleName("Switch connected cluster")
        self.cluster_picker.setMinimumWidth(150)
        self.cluster_picker.setToolTip("Switch between connected clusters")
        self.cluster_picker.activated.connect(self._pick_cluster)
        nav_dock.setTitleBarWidget(title)
        nav_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        nav_panel = QWidget()
        nav_panel.setObjectName("navigatorPanel")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(10, 12, 10, 10)
        nav_layout.setSpacing(8)
        cluster_heading = QLabel("CLUSTERS")
        heading_font = cluster_heading.font()
        heading_font.setPointSizeF(8.5)
        heading_font.setBold(True)
        cluster_heading.setFont(heading_font)
        theme.muted(cluster_heading)
        search_clusters = QToolButton()  # on the heading line, away from the switcher, so the pair is not read as one field
        search_clusters.setIcon(action_icon("search"))
        search_clusters.setAccessibleName("Search clusters")
        search_clusters.setToolTip("Show or hide cluster search")
        search_clusters.setCheckable(True)
        self._cluster_search_button = search_clusters
        heading_row = QHBoxLayout()
        heading_row.setSpacing(5)
        heading_row.addWidget(cluster_heading)
        heading_row.addStretch(1)
        heading_row.addWidget(search_clusters)
        nav_layout.addLayout(heading_row)
        nav_layout.addWidget(self.cluster_picker)
        self.cluster_search = QLineEdit()
        self.cluster_search.setObjectName("clusterSearch")
        self.cluster_search.setAccessibleName("Search clusters")
        self.cluster_search.setPlaceholderText("Search clusters…")
        self.cluster_search.setClearButtonEnabled(True)
        self.cluster_search.textChanged.connect(self.nav.set_filter)
        self.cluster_search.hide()
        def toggle_cluster_search(visible):
            self.cluster_search.setVisible(visible)
            if visible:
                self.cluster_search.setFocus()
            else:
                self.cluster_search.clear()
        search_clusters.toggled.connect(toggle_cluster_search)
        nav_layout.addWidget(self.cluster_search)
        nav_layout.addWidget(self.nav, 1)
        nav_dock.setWidget(nav_panel)
        nav_dock.setMinimumWidth(218)
        nav_dock.setMaximumWidth(360)
        self.addDockWidget(Qt.LeftDockWidgetArea, nav_dock)
        self.resizeDocks([nav_dock], [234], Qt.Horizontal)
        self.nav.context_clicked.connect(self.open_cluster)
        self.nav.connect_requested.connect(self.open_cluster)
        self.nav.disconnect_requested.connect(self._discard)
        self.nav.cancel_retry_requested.connect(self._cancel_background_connection)
        self.nav.retry_pending = lambda ctx: ctx in self._connection_retries
        self.nav.reconnect_requested.connect(self.reconnect_cluster)
        self.nav.view_requested.connect(self._open_view)
        self.nav.plugin_entries = lambda: [(p.id, f"{p.name or p.id}: settings for this cluster…")
                                           for p in self.plugins.cluster_plugins()]
        self.nav.plugin_settings_requested.connect(self.open_cluster_settings)
        self.nav.alias_requested.connect(self.rename_cluster_alias)
        self.nav.metrics_source_requested.connect(self.open_metrics_source)
        self.nav.sign_in_requested.connect(self.sign_in)
        self.nav.config_requested.connect(self.show_context_entry)
        self.nav.auto_refresh_requested.connect(self._navigator_auto_refresh)
        self.nav.auto_refresh_clusters_requested.connect(self._set_clusters_auto_refresh)

        self.status = self.statusBar()
        self.plugin_errors_button = QToolButton(self)
        self.plugin_errors_button.setObjectName("pluginErrorsStatus")
        self.plugin_errors_button.setText("Plugin error")
        self.plugin_errors_button.setToolTip("Open the Plugins settings to review plugin errors")
        self.plugin_errors_button.clicked.connect(self._open_plugin_errors)
        self.status.addPermanentWidget(self.plugin_errors_button)
        self.plugins.errors_changed.connect(self._sync_plugin_error_status)
        self.plugins.changed.connect(self._sync_plugin_error_status)
        self._sync_plugin_error_status()
        # Nothing connects by itself: the kubeconfig's current-context is not opened any more. Clusters the user asked
        # to remember when exiting come back through restore_clusters(), which the entry point calls.
        self.contexts, _ = self.new_service().load_contexts()
        self.nav.set_contexts(self.contexts, self.groups, self.aliases.aliases)
        self._sync_cluster_picker()
        self._update_plugins_menu()
        self.nav.groups_changed.connect(self._save_groups)
        self.status.showMessage("Double-click a cluster on the left to connect" if self.contexts
                                else self._no_contexts_message())

    def createPopupMenu(self):  # noqa: N802
        """Suppress the default dock and toolbar menu; Klotse exposes those controls elsewhere."""
        return None

    def _save_groups(self):
        try:
            self.groups.save()
        except OSError as error:
            QMessageBox.warning(self, "Cannot save the cluster groups", str(error))

    def sign_in(self, context):
        """Run the kubeconfig's sign-in helper in a terminal the user can answer (after a Yes); connect again afterwards."""
        argv = sign_in_argv(auth_info(self.kubeconfig_settings.path, context))
        if not argv:
            QMessageBox.information(self, "Sign in", f"The kubeconfig entry for {context} names no sign-in helper.")
            return
        shown = " ".join(argv)
        if QMessageBox.question(self, "Sign in", f"Klotse will open a terminal and run:\n\n{shown}\n\n"
                                "Finish the sign-in there, then connect to this cluster again. Run it?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        worker = Worker(open_terminal, argv, self.terminal_settings.mac_terminal)
        worker.setAutoDelete(False)  # kept until the terminal has been asked to open
        self._sign_in_workers = getattr(self, "_sign_in_workers", []) + [worker]
        worker.signals.error.connect(lambda message: QMessageBox.warning(self, "Sign in", f"The terminal could not be opened.\n\n{message}"))
        QThreadPool.globalInstance().start(worker)
    def show_context_entry(self, context):
        """Read-only: what the kubeconfig says about this context (names, server, sign-in kind; no secret values)."""
        from ..k8s.context_entry import entry_lines
        from .context_dialog import ContextEntryDialog
        dialog = ContextEntryDialog(context, entry_lines(self.kubeconfig_settings.path, context), self)
        dialog.exec()

    def rename_cluster_alias(self, context):
        current = self.aliases.display(context)
        alias, accepted = QInputDialog.getText(self, "Rename cluster alias", f"Display name for {context} (blank clears it):", text=current)
        if not accepted:
            return False
        try:
            self.aliases.set(context, alias)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save cluster alias", str(error))
            return False
        self.nav.set_alias(context, self.aliases.display(context))
        self._sync_cluster_picker()
        ws = self.workspaces.get(context)
        if ws is not None and context not in self._detached:
            self.clusters.setTabText(self.clusters.indexOf(ws), self.aliases.display(context))
        if ws is not None:
            self._update_scope_title(ws)
        return True

    def new_service(self):
        """A fresh service for one cluster, reading the custom kubeconfig when one is selected in Preferences."""
        service = self.factory()
        if self.kubeconfig_settings.path:
            service.kubeconfig = self.kubeconfig_settings.path
        return service

    def _no_contexts_message(self):
        path = self.kubeconfig_settings.path
        return f"No kubeconfig contexts found in {path}" if path else "No kubeconfig contexts found"

    # ---- clusters ------------------------------------------------------
    def _sync_cluster_picker(self, preferred=None):
        contexts = list(self.workspaces)
        self.cluster_picker.blockSignals(True)
        self.cluster_picker.clear()
        for context in contexts:  # shown by alias; the context name is the item's data, which is what selecting it uses
            self.cluster_picker.addItem(self.aliases.display(context), context)
        if not contexts:  # nothing connected: say so, and open the search so a cluster can be found and connected
            self.cluster_picker.addItem("No cluster connected", "")
            self.cluster_picker.setEnabled(False)
        else:
            self.cluster_picker.setEnabled(True)
        current = preferred
        if current is None:
            active = QApplication.activeWindow()
            current = next((context for context, window in self._detached.items() if window is active), None)
        if current is None and self.clusters.currentWidget() is not None:
            current = self.clusters.currentWidget().context
        index = self.cluster_picker.findData(current or "")
        if index >= 0:
            self.cluster_picker.setCurrentIndex(index)
        self.cluster_picker.blockSignals(False)

    def _update_scope_title(self, ws):
        """Keep the window title explicit about the cluster and resource scope in front."""
        alias = self.aliases.display(ws.context)
        view = ws.tabs.tabText(ws.tabs.currentIndex()) if ws.tabs.count() else "Overview"
        key = ws.foreground_key() if ws.tabs.count() else "overview"
        scoped = key in ws.loaders and ws.loaders[key][1]
        namespace = ws.namespace() if scoped else "all namespaces"
        title = f"{alias} ({ws.context}) — {view} — {namespace} — Klotse"
        window = self._detached.get(ws.context)
        if window is not None:
            window.setWindowTitle(title)
        if self.clusters.currentWidget() is ws:
            self.setWindowTitle(title)

    def _pick_cluster(self, index):
        context = self.cluster_picker.itemData(index)
        workspace = self.workspaces.get(context)
        if workspace is not None and context not in self._detached:
            self.clusters.setCurrentWidget(workspace)
        self.nav.reveal_cluster(context)

    def _cluster_tab_menu(self, position):
        index = self.clusters.tabBar().tabAt(position)
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("Detach cluster", lambda i=index: self._detach_cluster(i))
        menu.exec(self.clusters.tabBar().mapToGlobal(position))

    def _detach_cluster(self, index):
        """Move one cluster workspace into its own window; closing that window reattaches it as a tab."""
        ws = self.clusters.widget(index)
        if ws is None or ws.context in self._detached:
            return False

        owner = self

        class DetachedClusterWindow(QMainWindow):
            def focusInEvent(window, event):  # noqa: N802
                owner._sync_cluster_picker(ws.context)
                owner._update_scope_title(ws)
                super().focusInEvent(event)

            def closeEvent(window, event):  # noqa: N802
                workspace = window.takeCentralWidget()
                if workspace is not None:
                    workspace.setParent(owner.clusters)
                    tab = owner.clusters.addTab(workspace, workspace.context)
                    owner.clusters.setCurrentIndex(tab)
                owner._detached.pop(ws.context, None)
                event.accept()

        self.clusters.removeTab(index)
        window = DetachedClusterWindow()
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.setWindowTitle(f"{self.aliases.display(ws.context)} ({ws.context}) — Klotse")
        window.setCentralWidget(ws)
        ws.show()  # removeTab() hides the page; reparenting does not reliably show it on every Qt platform
        self._detached[ws.context] = window
        self._update_scope_title(ws)
        self._sync_cluster_picker(ws.context)
        window.resize(self.size())
        window.show()
        return True

    def restore_clusters(self):
        """Reconnect the clusters remembered at the last exit (those still in the kubeconfig); returns them."""
        remembered, current = self.session.saved()
        wanted = [ctx for ctx in remembered if ctx in self.contexts]
        spaces = self.session.namespaces()
        for ctx in wanted:
            self.open_cluster(ctx)
            if ctx in spaces and ctx in self.workspaces:
                self.workspaces[ctx].restore_namespace = spaces[ctx]  # applied when the namespace list arrives
        if current in wanted:
            self.open_cluster(current)  # focus the one that was in front
        if wanted:
            self.status.showMessage(f"Reconnecting {', '.join(wanted)}")
        return wanted

    def workspace(self, context=None):
        """The workspace for `context`, or the one in the current tab."""
        return self.workspaces.get(context) if context else self.clusters.currentWidget()

    def open_cluster(self, ctx, background=False):
        """Open `ctx` in its own tab, or focus it if it is already open."""
        if self._shutting_down:
            return
        if not background:
            self._cancel_connection_retry(ctx)
            self._clear_connection_prompt(ctx)
        if ctx in self.workspaces:
            self.clusters.setCurrentWidget(self.workspaces[ctx])
            return
        ws = ClusterWorkspace(self.new_service(), ctx, label_settings=self.label_settings,
                              terminal_settings=self.terminal_settings, plugins=self.plugins, audit_log=self.audit_log)
        self.workspaces[ctx] = ws
        self._apply_global_auto_pause(ws)
        ws.detail.set_show_managed_fields(self.managed_fields_action.isChecked())
        self.managed_fields_action.toggled.connect(ws.detail.set_show_managed_fields)
        ws.message.connect(self.status.showMessage)
        ws.state.connect(lambda s, c=ctx: self._on_state(c, s))
        ws.failed.connect(lambda m, c=ctx: self._on_failed(c, m))
        ws.view_opened.connect(lambda key, c=ctx: self._on_view_opened(c, key))
        ws.view_opened.connect(lambda _key, w=ws: self._update_scope_title(w))
        ws.ns_combo.currentIndexChanged.connect(lambda _index, w=ws: self._update_scope_title(w))
        ws.karpenter_found.connect(lambda entries, c=ctx: self.nav.show_karpenter(c, entries))
        ws.gateway_found.connect(lambda entries, c=ctx: self.nav.show_gateway(c, entries))
        ws.crd_groups_found.connect(lambda entries, c=ctx: self.nav.show_crd_groups(c, entries))
        ws.customize_requested.connect(self.customize_labels)  # the Columns… button on Nodes / Pods tabs
        index = self.clusters.addTab(ws, self.aliases.display(ctx))
        if not background:
            self.clusters.setCurrentIndex(index)
        ws.read_only_changed.connect(lambda _on, w=ws: self._retitle(w))
        ws.set_read_only(self.read_only_clusters.is_read_only(ctx))
        ws.review_changes = self.review_action.isChecked()
        ws.big_changed.connect(lambda _on, w=ws: self._retitle(w))
        ws.big_detected.connect(lambda nodes, pods, w=ws: self._found_big(w, nodes, pods))
        ws.set_big(self.big_clusters.is_big(ctx))
        ws.start()

    def _retitle(self, ws):
        """The cluster's tab title: its name, then 🔒 for read-only and (big) for big-cluster mode."""
        marks = (" 🔒" if ws.read_only else "") + (" (big)" if ws.big else "")
        self.clusters.setTabText(self.clusters.indexOf(ws), self.aliases.display(ws.context) + marks)

    def _cluster_names(self, context):
        """(short name, full name) of a cluster for messages: the alias, and the kubeconfig context in brackets when they differ."""
        short = self.aliases.display(context)
        return short, (short if short == context else f"{short} ({context})")

    def _found_big(self, ws, nodes, pods):
        """The Overview showed a big cluster. Unless the user chose before, remember it, switch the lighter mode on and say so."""
        try:
            news = self.big_clusters.detected(ws.context)
        except OSError:
            news = True  # not saved, but still used for this session
        if news:
            ws.set_big(True)
            self._maybe_big_notice(ws)
            _short, full = self._cluster_names(ws.context)
            self.status.showMessage(f"{full}: {nodes} nodes, {pods} pods, so big-cluster mode is on (View > Big cluster mode). "
                                    "The Overview now shows Warning events only and reloads less often.", 15000)

    def _maybe_big_notice(self, ws):
        """Tell the user, once per connection, that a big cluster is read in a lighter way and what that changes, unless they ticked
        "Don't show this again" for this cluster. Not modal (the window stays usable); nothing appears in a window that is not shown.
        The name of the cluster is in the text, not only in the title (some platforms do not show a dialog's title)."""
        if ws is None or not ws.big or not self.isVisible() or self.big_clusters.notice_hidden(ws.context):
            return
        shown = self.__dict__.setdefault("_big_notices", {})
        if ws.context in shown:
            return
        short, full = self._cluster_names(ws.context)
        text = NOTICE_TEXT.replace("This cluster is big", f"{full} is big", 1)
        box = QMessageBox(QMessageBox.Information, f"{short}: big cluster", text, QMessageBox.Ok, self)
        again = QCheckBox("Don't show this again for this cluster")
        box.setCheckBox(again)
        box.setModal(False)
        shown[ws.context] = box

        def finished(_result, c=ws.context):
            shown.pop(c, None)
            if again.isChecked():
                try:
                    self.big_clusters.hide_notice(c)
                except OSError as error:
                    QMessageBox.warning(self, "Cannot save", str(error))
            box.deleteLater()

        box.finished.connect(finished)
        box.show()

    def set_big_mode(self, on):
        """View > Big cluster mode for the cluster in front: the user's explicit choice (on or off), kept for next time."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return
        try:
            self.big_clusters.set_mode(ws.context, "on" if on else "off")
        except OSError as error:
            QMessageBox.warning(self, "Cannot save", str(error))
        ws.set_big(on)
        ws.refresh()

    def _sync_big_action(self):
        ws = self.clusters.currentWidget()
        self.big_action.setEnabled(ws is not None)
        self.big_action.setChecked(bool(ws is not None and ws.big))

    def _set_global_auto_paused(self, paused):
        if paused:
            if self._global_auto_paused:
                return
            self._global_auto_snapshot = {
                context: (workspace.auto_action.isChecked(), workspace._auto_ms)
                for context, workspace in self.workspaces.items()
            }
            self._global_auto_paused = True
            for workspace in self.workspaces.values():
                workspace.set_global_auto_paused(True)
        elif self._global_auto_paused:
            self._restore_global_auto_refresh()

    def _apply_global_auto_pause(self, workspace):
        """Keep clusters opened during a global pause stopped and include them in the resume snapshot."""
        if not self._global_auto_paused:
            return
        if self._global_auto_snapshot is None:
            self._global_auto_snapshot = {}
        self._global_auto_snapshot.setdefault(workspace.context, (workspace.auto_action.isChecked(), workspace._auto_ms))
        workspace.set_global_auto_paused(True)

    def _restore_global_auto_refresh(self):
        if not self._global_auto_paused and self._global_auto_snapshot is None:
            return
        snapshot = self._global_auto_snapshot or {}
        self._global_auto_paused = False
        self._global_auto_snapshot = None
        for context, workspace in self.workspaces.items():
            enabled, interval = snapshot.get(context, (True, workspace._auto_ms))
            workspace.apply_auto_refresh_state(enabled, interval)
        blocker = QSignalBlocker(self.pause_auto_refresh_all_action)
        self.pause_auto_refresh_all_action.setChecked(False)
        del blocker

    def _enable_auto_refresh_all(self):
        snapshot = self._global_auto_snapshot or {}
        self._global_auto_paused = False
        self._global_auto_snapshot = None
        for context, workspace in self.workspaces.items():
            _was_enabled, interval = snapshot.get(context, (True, workspace._auto_ms))
            workspace.apply_auto_refresh_state(True, interval)
        blocker = QSignalBlocker(self.pause_auto_refresh_all_action)
        self.pause_auto_refresh_all_action.setChecked(False)
        del blocker

    def _navigator_auto_refresh(self, choice):
        """The CONNECTED section's menu: the same actions as View > Settings > Auto-refresh across clusters."""
        if choice == "pause":
            self.pause_auto_refresh_all_action.setChecked(True)
        elif choice == "enable":
            self._enable_auto_refresh_all()
        elif choice == "restore":
            self._restore_global_auto_refresh()

    def _set_clusters_auto_refresh(self, contexts, enabled):
        """The cluster menu: turn auto-refresh on or off for the given connected clusters, through their own toggle (so a global pause still wins)."""
        for context in contexts:
            workspace = self.workspaces.get(context)
            if workspace is not None:
                workspace.auto_action.setChecked(bool(enabled))

    def _sync_global_auto_actions(self):
        available = bool(self.workspaces) or self._global_auto_snapshot is not None
        self.pause_auto_refresh_all_action.setEnabled(available)
        blocker = QSignalBlocker(self.pause_auto_refresh_all_action)
        self.pause_auto_refresh_all_action.setChecked(self._global_auto_paused)
        del blocker
        self.restore_auto_refresh_action.setEnabled(self._global_auto_snapshot is not None)
        self.enable_auto_refresh_all_action.setEnabled(bool(self.workspaces))

    # ---- Plugins menu ---------------------------------------------------
    def _update_plugins_menu(self):
        self.plugins_menu.menuAction().setVisible(bool(self.plugins.cluster_plugins()))

    def _plugin_errors(self):
        found = {item.id: item for item in self.plugins.loaded}
        errors = dict(self.plugins.errors)
        for plugin_id, item in found.items():
            if item.error:
                errors.setdefault(plugin_id, item.error)
        return [(plugin_id, (found[plugin_id].plugin or found[plugin_id].manifest).name
                 if plugin_id in found and (found[plugin_id].plugin or found[plugin_id].manifest) else plugin_id, error)
                for plugin_id, error in sorted(errors.items())]

    def _sync_plugin_error_status(self, *_):
        errors = self._plugin_errors()
        count = len(errors)
        self.plugin_errors_button.setVisible(bool(count))
        self.plugin_errors_button.setText("Plugin error" if count == 1 else f"Plugin errors ({count})")
        self.plugin_errors_button.setToolTip("\n".join(f"{name}: {error}" for _plugin_id, name, error in errors)
                                              or "No plugin errors")
        color = theme.BAD
        self.plugin_errors_button.setStyleSheet(f"QToolButton#pluginErrorsStatus {{ color: {color}; font-weight: 600; }}")

    def _open_plugin_errors(self):
        errors = self._plugin_errors()
        if errors:
            self.open_preferences(errors[0][0])

    def _fill_plugins_menu(self):
        """One entry per enabled plugin with cluster settings, for the cluster in front (disabled without one)."""
        self.plugins_menu.clear()
        ws = self.clusters.currentWidget()
        for plugin in self.plugins.cluster_plugins():
            action = self.plugins_menu.addAction(f"{plugin.name or plugin.id}: settings for this cluster…")
            action.setEnabled(ws is not None)
            action.triggered.connect(lambda _=False, p=plugin: self.open_cluster_settings(p.id))
        if ws is None and self.plugins_menu.actions():
            self.plugins_menu.addSeparator()
            self.plugins_menu.addAction("Connect to a cluster first").setEnabled(False)

    def _sync_read_only_action(self):
        ws = self.clusters.currentWidget()
        self.read_only_action.setEnabled(ws is not None)
        self.read_only_action.setChecked(bool(ws is not None and ws.read_only))

    def set_read_only(self, on):
        """View > Read-only mode for the cluster in front. Turning it on is immediate; turning it off asks first (Yes/No,
        default No), because the point of the mode is that a slip cannot change a production cluster. The choice is saved
        for that context; a save error is shown and the state stays as it was."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        if not on and QMessageBox.question(
                self, "Allow changes", f"Cluster: {ws.context}\nAllow changes to this cluster again?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self._sync_read_only_action()
            return False
        try:
            self.read_only_clusters.set_read_only(ws.context, on)
        except OSError as e:
            QMessageBox.warning(self, "Read-only mode", str(e))
            self._sync_read_only_action()
            return False
        ws.set_read_only(on)
        self._sync_read_only_action()
        return True

    def show_apply_yaml(self):
        """File > Apply YAML…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_apply_yaml()
        return True

    def show_search(self):
        """View > Search objects…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_search()
        return True

    def show_audit_log(self):
        """View > Change log…: the local record of changes made from Klotse (all clusters)."""
        dialog = AuditDialog(self.audit_log, self)
        dialog.exec()
        dialog.deleteLater()
        return True

    def show_selector_logs(self):
        """View > Logs by label selector…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.logs_by_selector()
        return True

    def export_diagnostics(self):
        """View > Export diagnostics…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.export_diagnostics()
        return True

    def show_rbac(self):
        """View > RBAC lookup…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_rbac()
        return True

    def show_rightsizing(self):
        """View > Right-sizing…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_rightsizing()
        return True

    def show_port_forwards(self):
        """View > Operations > Port forwards…: the Port forwards tab of the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_port_forwards()
        return True

    def show_cluster_metrics_query(self):
        """View > Insights > Metrics query…: a query window for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_metrics_query()
        return True

    # ---- command palette -------------------------------------------------------------------------------------
    def palette_entries(self):
        """Everything the palette can do right now: the views of the cluster in front, its namespaces, the clusters of the kubeconfig and
        the enabled menu actions. Rebuilt each time it opens, so it reflects what is connected and enabled."""
        from .navigator import VIEWS
        entries = []
        ws = self.clusters.currentWidget()
        if ws is not None:
            for entry in VIEWS:
                leaves = [(entry[0], entry[1])] if len(entry) == 2 else entry[1]
                group = "" if len(entry) == 2 else entry[0]
                for key, label in leaves:
                    entries.append(Entry("Go to", label, f"{group} · {ws.context}" if group else ws.context,
                                         lambda k=key, w=ws: self._goto_view(w, k), f"view:{ws.context}:{key}"))
            for name in getattr(ws, "_ns_names", []):
                if name != "All namespaces":
                    entries.append(Entry("Namespace", name, ws.context, lambda n=name, w=ws: w.set_namespace(n),
                                         f"namespace:{ws.context}:{name}"))
            entries.append(Entry("Namespace", "All namespaces", ws.context,
                                 lambda w=ws: w.set_namespace("All namespaces"), f"namespace:{ws.context}:All namespaces"))
        for ctx in self.contexts:
            if ctx in self.workspaces:
                entries.append(Entry("Cluster", ctx, "switch to it", lambda c=ctx: self._focus_workspace(self.workspaces[c]),
                                     f"cluster:{ctx}"))
            else:
                entries.append(Entry("Cluster", ctx, "connect", lambda c=ctx: self.open_cluster(c), f"cluster:{ctx}"))
        def add_menu_actions(menu, path):
            menu.aboutToShow.emit()  # menus enable their entries lazily, when they are about to show
            for action in menu.actions():
                text = action.text().replace("&", "").replace("…", "").strip()
                if action.isSeparator() or not text or action is self.palette_action:
                    continue
                if action.menu() is not None:
                    add_menu_actions(action.menu(), f"{path} · {text}")
                elif action.isEnabled():
                    entries.append(Entry("Action", text, f"{path} menu" + (" · checked" if action.isCheckable() and action.isChecked() else ""), action.trigger))

        for menu_action in self.menuBar().actions():
            if menu_action.menu() is not None:
                add_menu_actions(menu_action.menu(), menu_action.text().replace("&", ""))
        recent_keys = self._recent_palette_keys()
        available = {entry.key: entry for entry in entries if entry.key}
        # Recent targets can belong to another open workspace. Resolve them against every live workspace, while
        # keeping the ordinary palette list scoped to the cluster currently in front.
        for other in self.workspaces.values():
            if other is ws:
                continue
            for entry in VIEWS:
                leaves = [(entry[0], entry[1])] if len(entry) == 2 else entry[1]
                group = "" if len(entry) == 2 else entry[0]
                for key, label in leaves:
                    target_key = f"view:{other.context}:{key}"
                    available[target_key] = Entry("Go to", label, f"{group} · {other.context}" if group else other.context,
                                                   lambda k=key, w=other: self._goto_view(w, k), target_key)
            for name in getattr(other, "_ns_names", []):
                if name != "All namespaces":
                    target_key = f"namespace:{other.context}:{name}"
                    available[target_key] = Entry("Namespace", name, other.context,
                                                   lambda n=name, w=other: (self._focus_workspace(w), w.set_namespace(n)), target_key)
            target_key = f"namespace:{other.context}:All namespaces"
            available[target_key] = Entry("Namespace", "All namespaces", other.context,
                                           lambda w=other: (self._focus_workspace(w), w.set_namespace("All namespaces")), target_key)
        recent = []
        kept = [key for key in recent_keys if key in available][:8]
        if kept != recent_keys:
            self._save_recent_palette_keys(kept)
        for key in kept:
            entry = available[key]
            recent.append(Entry("Recent", entry.label, f"{entry.category} · {entry.detail}",
                                lambda k=key, run=entry.run: self._run_palette_target(k, run), key))
        if recent:
            recent.append(Entry("Recent", "Clear recent entries", "remove saved palette history", self.clear_palette_history))
        entries = recent + entries
        for entry in entries:
            if entry.key and entry.category in ("Go to", "Namespace", "Cluster") and entry.run:
                action = entry.run
                entry.run = lambda run=action, key=entry.key: self._run_palette_target(key, run)
        return entries

    def _recent_palette_keys(self):
        try:
            value = json.loads(self.label_settings.store.value("palette/recent", "[]"))
        except (TypeError, ValueError):
            return []
        return value[:8] if isinstance(value, list) and all(isinstance(item, str) for item in value) else []

    def _save_recent_palette_keys(self, keys):
        self.label_settings.store.setValue("palette/recent", json.dumps(keys[:8]))
        self.label_settings.store.sync()

    def _run_palette_target(self, key, action):
        keys = [key] + [item for item in self._recent_palette_keys() if item != key]
        self._save_recent_palette_keys(keys)
        return action()

    def clear_palette_history(self):
        self._save_recent_palette_keys([])

    def _goto_view(self, ws, key):
        self._focus_workspace(ws)
        ws.open_view(key)

    def _focus_workspace(self, ws):
        detached = self._detached.get(ws.context)
        if detached is not None:
            detached.show()
            detached.raise_()
            detached.activateWindow()
            self._sync_cluster_picker(ws.context)
        else:
            self.clusters.setCurrentWidget(ws)

    def show_palette(self):
        """View > Command palette… (Ctrl+K): one search box over views, namespaces, clusters and actions. What is typed that matches
        nothing can be searched for as an object name (View > Search objects…)."""
        def search_for(text):
            ws = self.clusters.currentWidget()
            if ws is None:
                return None

            def run(w=ws, t=text):
                dialog = w.show_search()
                dialog.box.setText(t)
            return Entry("Search", f"Search objects for “{text}”", ws.context, run)

        dialog = PaletteDialog(self.palette_entries(), self, free_text=search_for)
        dialog.exec()
        dialog.deleteLater()

    def set_review_changes(self, on):
        """View > Review changes before saving: remembered, and applied to every open cluster and to those opened later."""
        self.label_settings.store.setValue("edit/review_changes", bool(on))
        self.label_settings.store.sync()
        for ws in self.workspaces.values():
            ws.review_changes = bool(on)

    def show_trivy(self):
        """View > Trivy reports…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_trivy()
        return True

    def show_multi(self):
        """View > Several clusters…: one list of a kind from several open clusters. A new window each time, so it lists the clusters that are
        open now; False when no cluster is open."""
        if not self.workspaces:
            return False
        old = getattr(self, "_multi_dialog", None)
        if old is not None:
            old.close()
            old.deleteLater()
        dialog = self._multi_dialog = MultiClusterDialog(_MultiSource(self), self)
        dialog.setWindowFlag(Qt.Window, True)
        dialog.go_to_requested.connect(self._goto_cluster_object)
        dialog.action_requested.connect(self._multi_cluster_action)
        dialog.show()
        return True

    def _multi_cluster_action(self, kind, action, rows):
        from .resource_table import LOG_SOURCES, RESTARTABLE

        if not ((action == "restart" and kind in RESTARTABLE) or (action == "logs" and kind in LOG_SOURCES)):
            return
        groups = {}
        for row in rows:
            groups.setdefault(row.cluster, []).append(row)
        closed = []
        for context, selected in groups.items():
            workspace = self.workspaces.get(context)
            if workspace is None:
                closed.append(context)
                continue
            if action == "restart":
                workspace.bulk_action(kind, "restart", selected)
            else:
                for row in selected:
                    workspace.multi_logs(kind, row)
        if closed:
            self.status.showMessage("Clusters no longer open: " + ", ".join(closed), 6000)

    def _goto_cluster_object(self, cluster, kind, namespace, name):
        ws = self.workspaces.get(cluster)
        if ws is None:
            self.status.showMessage(f"{cluster} is no longer open", 6000)
            return
        self.clusters.setCurrentWidget(ws)
        self.activateWindow()
        ws.go_to_object(kind, namespace, name)

    def show_security(self):
        """View > Pod security…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_security()
        return True

    def show_preflight(self):
        """View > Upgrade preflight…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_preflight()
        return True

    def show_unused(self):
        """View > Unused objects…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_unused()
        return True

    def show_health(self):
        """View > Health check…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_health()
        return True

    def show_capacity(self):
        """View > Capacity…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_capacity()
        return True

    def show_permissions(self):
        """View > My permissions…, for the cluster in front; False when no cluster is open."""
        ws = self.clusters.currentWidget()
        if ws is None:
            return False
        ws.show_permissions()
        return True

    def open_metrics_source(self, context):
        """Cluster list > Metrics source…: the Prometheus / VictoriaMetrics source of usage history for that cluster (the same dialog as
        Usage history > Source…). False when the cluster is not open."""
        ws = self.workspaces.get(context)
        if ws is None:
            return False
        ws.configure_metrics_source()
        return True

    def open_cluster_settings(self, plugin_id, context=None):
        """Edit a plugin's settings for `context` (default: the cluster in front); False when there is no such open
        cluster or the plugin is not enabled."""
        ws = self.workspaces.get(context) if context else self.clusters.currentWidget()
        plugin = next((p for p in self.plugins.cluster_plugins() if p.id == plugin_id), None)
        if ws is None or plugin is None:
            return False
        dialog = ClusterSettingsDialog(self.plugins, plugin, ws.context, self)
        accepted = dialog.exec() == QDialog.Accepted
        dialog.deleteLater()
        return accepted

    def config_folder(self):
        """Folder holding Klotse's settings file (see LabelSettings.store.fileName())."""
        return Path(self.label_settings.store.fileName()).parent

    def show_about(self):
        dialog = AboutDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def open_preferences(self, plugin_id=None):
        # One Preferences window for everything: General, Terminal and Plugins. macOS shows the entry in the application menu (Settings/Preferences, Cmd-,), elsewhere it is Edit.
        dialog = PreferencesDialog(self.kubeconfig_settings, self.config_folder(), self,
                                   terminal=self.terminal_settings,
                                   plugins=self.plugins, themes=self.theme_settings, debug=self.debug_settings,
                                   forward=ForwardDefaults(self.label_settings.store), query=QueryDefaults(self.label_settings.store))
        if plugin_id and dialog.plugins_panel is not None:
            dialog.tabs.setCurrentWidget(dialog.plugins_panel)
            for index in range(dialog.plugins_panel.list.count()):
                item = dialog.plugins_panel.list.item(index)
                if item.data(Qt.UserRole).id == plugin_id:
                    dialog.plugins_panel.list.setCurrentRow(index)
                    break
        accepted = dialog.exec() == PreferencesDialog.Accepted
        self.apply_timing_log()
        chosen = dialog.chosen_kubeconfig()
        chosen_theme = dialog.chosen_theme()
        dialog.deleteLater()
        if accepted and self.terminal_settings.embedded:
            QTimer.singleShot(500, embedded_terminal.warm_up)  # just switched on: start the engine now, not at the first shell
        if accepted and chosen_theme and chosen_theme != self.theme_settings.selected().name:
            self.apply_theme(chosen_theme)
        if accepted and chosen != self.kubeconfig_settings.path:
            self.apply_kubeconfig(chosen)

    def timing_log_path(self):
        return os.path.join(os.path.dirname(self.label_settings.store.fileName()), "timings.log")

    def apply_timing_log(self):
        """Switch the timing log on or off to match the Debug preference (or KLOTSE_TIMINGS=1)."""
        from .. import __display_version__
        timings.configure(self.timing_log_path(), self.debug_settings.timings, __display_version__)

    def apply_theme(self, name):
        """Save the chosen theme and apply it to the running application (no restart)."""
        try:
            self.theme_settings.set(name)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot save preferences", str(error))
            return False
        theme.apply(QApplication.instance(), self.theme_settings.selected())
        for workspace in self.workspaces.values():
            workspace.overview.refresh_theme()
            workspace.wl_overview.refresh_theme()
        return True

    def apply_kubeconfig(self, path):
        """Switch to another kubeconfig (None = default): close open clusters, then reload the context list."""
        if self.workspaces and QMessageBox.question(
                self, "Change kubeconfig", "Changing the kubeconfig closes all open clusters. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        try:
            self.kubeconfig_settings.set_path(path)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot save preferences", str(error))
            return False
        for ctx in list(self._connection_retries):
            self._cancel_connection_retry(ctx)
        for ctx in list(self._connection_prompts):
            self._clear_connection_prompt(ctx)
        for ctx in list(self.workspaces):
            self._discard(ctx)
        contexts, _ = self.new_service().load_contexts()
        self.contexts = contexts
        self.nav.set_contexts(contexts, self.groups, self.aliases.aliases)
        self.status.showMessage(
            f"Loaded {len(contexts)} context(s) from {path or 'the default kubeconfig'}" if contexts
            else self._no_contexts_message())
        return True

    def customize_labels(self, kind):
        available, available_annotations, builtin = set(), set(), ()
        for workspace in self.workspaces.values():
            for table in workspace.resource_tables():
                if table.kind == kind:
                    if table.customize_button is not None:  # not the Overview's compact drill-down tables
                        builtin = builtin or table.base_columns
                    for row in table.model.rows:
                        metadata = (row.raw or {}).get("metadata") or {}
                        available.update((metadata.get("labels") or {}).keys())
                        available_annotations.update((metadata.get("annotations") or {}).keys())
        extra = {"available_annotations": available_annotations} if kind in ANNOTATION_KINDS else {}
        dialog = LabelColumnsDialog(kind, self.label_settings, available, builtin, self, **extra)
        dialog.exec()
        dialog.deleteLater()

    def reconnect_cluster(self, ctx):
        self._discard(ctx)
        self.open_cluster(ctx)

    def _on_state(self, ctx, state):
        self.nav.set_state(ctx, state)
        if state == "connected":
            self._cancel_connection_retry(ctx)
            self.nav.show_views(ctx)
            QTimer.singleShot(0, lambda c=ctx: self._maybe_big_notice(self.workspaces.get(c)))

    def _on_failed(self, ctx, msg):
        background = ctx in self._connection_retries
        self._discard(ctx, preserve_retry=True)
        self.nav.set_state(ctx, "failed", msg)
        self.status.showMessage(f"{ctx}: {msg}")
        if background:
            self._schedule_connection_retry(ctx)
            return
        if self._shutting_down:
            return
        box = QMessageBox(QMessageBox.Warning, "Cannot connect", f"{ctx}\n\n{msg}", parent=self)
        retry = box.addButton("Retry now", QMessageBox.AcceptRole)
        later = box.addButton("Retry in background", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        box.setModal(False)
        self._connection_prompts[ctx] = box

        def answered(_result):
            if self._connection_prompts.get(ctx) is not box:
                return
            self._connection_prompts.pop(ctx, None)
            clicked = box.clickedButton()
            box.deleteLater()
            if self._shutting_down:
                return
            if clicked is retry:
                self.open_cluster(ctx)
            elif clicked is later:
                timer = QTimer(self, singleShot=True)
                timer.timeout.connect(lambda: self._retry_connection(ctx))
                self._connection_retries[ctx] = (timer, 0)
                self._schedule_connection_retry(ctx)

        box.finished.connect(answered)
        box.show()

    def _schedule_connection_retry(self, ctx):
        current = self._connection_retries.get(ctx)
        if current is None or self._shutting_down:
            return
        timer, attempts = current
        if attempts >= 5:
            self._cancel_connection_retry(ctx)
            self.status.showMessage(f"{ctx}: background connection retries stopped after 5 attempts")
            return
        delay = min(5 * (2 ** attempts), 60)
        self.status.showMessage(f"{ctx}: retrying in {delay}s (right-click cluster to cancel)")
        timer.start(delay * 1000)

    def _retry_connection(self, ctx):
        current = self._connection_retries.get(ctx)
        if current is None or self._shutting_down or ctx not in self.contexts:
            self._cancel_connection_retry(ctx)
            return
        timer, attempts = current
        timer.stop()
        if ctx in self.workspaces:
            return  # an attempt is already in flight
        self._connection_retries[ctx] = (timer, attempts + 1)
        self.open_cluster(ctx, background=True)

    def _cancel_connection_retry(self, ctx):
        current = self._connection_retries.pop(ctx, None)
        if current:
            current[0].stop()
            current[0].deleteLater()

    def _clear_connection_prompt(self, ctx):
        box = self._connection_prompts.pop(ctx, None)
        if box is not None:
            box.close()
            box.deleteLater()

    def _cancel_background_connection(self, ctx):
        self._cancel_connection_retry(ctx)
        if ctx in self.workspaces and not self.workspaces[ctx]._ready:
            self._discard(ctx)
        self.status.showMessage(f"{ctx}: background connection retry cancelled")

    def _close_cluster_tab(self, index):
        ws = self.clusters.widget(index)
        self._discard(ws.context)

    def _discard(self, ctx, preserve_retry=False):
        if not preserve_retry:
            self._cancel_connection_retry(ctx)
        ws = self.workspaces.pop(ctx, None)
        if ws is None:
            return
        self.clusters.removeTab(self.clusters.indexOf(ws))
        ws.shutdown()
        ws.deleteLater()
        self.nav.hide_views(ctx)
        self.nav.set_state(ctx, "idle")
        self._sync_cluster_picker()

    def _open_view(self, ctx, key):
        ws = self.workspaces.get(ctx)
        if ws is None:
            return
        self.clusters.setCurrentWidget(ws)
        ws.open_view(key)

    def _on_view_opened(self, ctx, key):
        if self.clusters.currentWidget() is self.workspaces.get(ctx):
            self.nav.select_view(ctx, key)

    def _fill_workspace_panels_menu(self):
        """Offer recovery actions in the app menu for the active workspace's closable docks."""
        self.panels_menu.clear()
        workspace = self.clusters.currentWidget()
        if workspace is None:
            action = self.panels_menu.addAction("Open a cluster to show its panels")
            action.setEnabled(False)
            return
        self.panels_menu.addAction(workspace.detail_dock.toggleViewAction())
        self.panels_menu.addAction(workspace.log_dock.toggleViewAction())

    def _on_cluster_changed(self, index):
        ws = self.clusters.widget(index)
        self.setWindowTitle(f"Klotse — {ws.context}" if ws else "Klotse")
        if ws is None:
            return
        self._update_scope_title(ws)
        if ws.tabs.count():
            self.nav.select_view(ws.context, ws.tabs.currentWidget().view_key)
        else:
            self.nav.select_cluster(ws.context)

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        for ctx in list(self._connection_retries):
            self._cancel_connection_retry(ctx)
        for ctx in list(self._connection_prompts):
            self._clear_connection_prompt(ctx)
        self.closing.emit()
        for ws in self.workspaces.values():
            ws.shutdown()
        QThreadPool.globalInstance().clear()
        shutdown_all()

    def confirm_exit(self):
        """Ask whether to reconnect the open clusters next time; False when the user cancels the exit.

        Yes remembers them, No (or no cluster open) leaves the next start with a clean list. A window that was never
        shown has nobody to ask."""
        if not self.isVisible():
            return True
        opened = [self.clusters.widget(i).context for i in range(self.clusters.count())]
        try:
            if not opened:
                self.session.forget()
                return True
            front = self.clusters.currentWidget()
            names = ", ".join(opened)
            answer = QMessageBox.question(
                self, "Exit Klotse",
                "\n".join(["Reconnect to the open cluster(s) the next time Klotse starts?", "", names, "",
                           "Yes remembers them; No starts with a clean list."]),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                return False
            if answer == QMessageBox.Yes:
                spaces = {self.clusters.widget(i).context: self.clusters.widget(i).namespace() for i in range(self.clusters.count())}
                self.session.remember(opened, front.context if front is not None else None, spaces)
            else:
                self.session.forget()
        except OSError as error:  # never trap the user in the app because the settings file is not writable
            QMessageBox.warning(self, "Cannot save the session", str(error))
        return True

    def closeEvent(self, event):
        if not self._shutting_down and not self.confirm_exit():
            event.ignore()
            return
        self.shutdown()
        super().closeEvent(event)
