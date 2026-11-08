import os
import sys
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDockWidget, QMainWindow, QMessageBox, QSizePolicy,
                               QTabWidget, QWidget)

from .about import AboutDialog
from .cluster_settings_dialog import ClusterSettingsDialog
from .cluster_groups import ClusterGroups
from .audit_log import AuditLog
from .audit_dialog import AuditDialog
from .cluster_session import ClusterSession
from ..plugins.manager import PluginManager
from .customizations import ANNOTATION_KINDS, LabelColumnsDialog, LabelSettings
from .edit_menu import EditActions
from .log_view import shutdown_all
from .navigator import Navigator
from ..k8s.timing import timings
from .preferences import DebugSettings, KubeconfigSettings, PreferencesDialog
from .big_clusters import NOTICE_TEXT, BigClusters
from .palette import Entry
from .palette_dialog import PaletteDialog
from .read_only import ReadOnlyClusters
from . import embedded_terminal, theme
from .theme import ThemeSettings
from .terminal_settings import TerminalSettings
from .workspace import ClusterWorkspace


class MainWindow(QMainWindow):
    """Shell: the cluster navigator on the left and one workspace tab per open cluster.

    `service_factory` is called once per cluster so every workspace gets its own connection.
    """

    closing = Signal()

    def __init__(self, service_factory, label_settings=None, plugin_manager=None):
        super().__init__()
        self.factory = service_factory
        self.workspaces = {}  # context name -> ClusterWorkspace
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
        # plugins: bundled, pip (entry points) and the plugins folder next to the settings file; all off until enabled
        self.plugins = plugin_manager if plugin_manager is not None else PluginManager(
            self.label_settings.store, folder=Path(self.label_settings.store.fileName()).parent / "plugins", parent=self)
        self.setWindowTitle("Klotse")
        self.resize(1500, 850)
        self._shutting_down = False
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence.Quit)
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
        self.preferences_action.setMenuRole(QAction.PreferencesRole)
        self.preferences_action.setShortcut(QKeySequence.Preferences)
        self.preferences_action.triggered.connect(self.open_preferences)
        self.edit_menu.addAction(self.preferences_action)
        self.managed_fields_action = QAction("Show managedFields in details", self, checkable=True, checked=False)
        self.managed_fields_action.setToolTip("Include metadata.managedFields in the Details pane (hidden by default)")
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.managed_fields_action)
        self.permissions_action = QAction("My permissions…", self)
        self.permissions_action.setToolTip("What you may do in the namespace of the cluster in front, and a Can I? check")
        self.permissions_action.triggered.connect(self.show_permissions)
        view_menu.addAction(self.permissions_action)
        self.search_action = QAction("Search objects…", self)
        self.search_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.search_action.setToolTip("Find objects by name, label, kind or namespace across the cluster in front")
        self.search_action.triggered.connect(self.show_search)
        view_menu.addAction(self.search_action)
        self.audit_action = QAction("Change log…", self)
        self.audit_action.setToolTip("What was changed from Klotse: when, which cluster, what, and whether the cluster accepted it")
        self.audit_action.triggered.connect(self.show_audit_log)
        view_menu.addAction(self.audit_action)
        self.selector_logs_action = QAction("Logs by label selector…", self)
        self.selector_logs_action.setToolTip("Follow the logs of every pod that matches a label selector, in one tab")
        self.selector_logs_action.triggered.connect(self.show_selector_logs)
        view_menu.addAction(self.selector_logs_action)
        view_menu.aboutToShow.connect(lambda: self.selector_logs_action.setEnabled(self.clusters.currentWidget() is not None))
        self.diagnostics_action = QAction("Export diagnostics…", self)
        self.diagnostics_action.setToolTip("Save the manifests, events and logs of the namespace in front as a zip file (no Secrets)")
        self.diagnostics_action.triggered.connect(self.export_diagnostics)
        view_menu.addAction(self.diagnostics_action)
        view_menu.aboutToShow.connect(lambda: self.diagnostics_action.setEnabled(self.clusters.currentWidget() is not None))
        self.rbac_action = QAction("RBAC lookup…", self)
        self.rbac_action.setToolTip("Who can do something, and what a user, group or service account can do")
        self.rbac_action.triggered.connect(self.show_rbac)
        view_menu.addAction(self.rbac_action)
        view_menu.aboutToShow.connect(lambda: self.rbac_action.setEnabled(self.clusters.currentWidget() is not None))
        self.rightsizing_action = QAction("Right-sizing…", self)
        self.rightsizing_action.setToolTip("Requests and limits against current usage, per workload, with advice")
        self.rightsizing_action.triggered.connect(self.show_rightsizing)
        view_menu.addAction(self.rightsizing_action)
        view_menu.aboutToShow.connect(lambda: self.rightsizing_action.setEnabled(self.clusters.currentWidget() is not None))
        self.palette_action = QAction("Command palette…", self)
        self.palette_action.setShortcut(QKeySequence("Ctrl+K"))
        self.palette_action.setToolTip("Jump to a view, namespace or cluster, or run an action, from one search box")
        self.palette_action.triggered.connect(self.show_palette)
        view_menu.addAction(self.palette_action)
        self.review_action = QAction("Review changes before saving", self, checkable=True)
        self.review_action.setToolTip("Before an edit is stored, ask the server for a dry run and show what would change (nothing is stored by the preview)")
        self.review_action.setChecked(str(self.label_settings.store.value("edit/review_changes", "true")).lower() != "false")
        self.review_action.toggled.connect(self.set_review_changes)
        view_menu.addAction(self.review_action)
        self.trivy_action = QAction("Trivy reports…", self)
        self.trivy_action.setToolTip("Vulnerability, config audit, exposed secret and RBAC reports written by the Trivy Operator (read only)")
        self.trivy_action.triggered.connect(self.show_trivy)
        view_menu.addAction(self.trivy_action)
        view_menu.aboutToShow.connect(lambda: self.trivy_action.setEnabled(self.clusters.currentWidget() is not None))
        self.security_action = QAction("Pod security…", self)
        self.security_action.setToolTip("Workloads that break the Pod Security Standards: privileged, host access, hostPath, running as root")
        self.security_action.triggered.connect(self.show_security)
        view_menu.addAction(self.security_action)
        view_menu.aboutToShow.connect(lambda: self.security_action.setEnabled(self.clusters.currentWidget() is not None))
        self.preflight_action = QAction("Upgrade preflight…", self)
        self.preflight_action.setToolTip("Before an upgrade or a drain: deprecated APIs, PodDisruptionBudgets that allow no disruption, "
                                         "kubelet version skew, certificate dates")
        self.preflight_action.triggered.connect(self.show_preflight)
        view_menu.addAction(self.preflight_action)
        view_menu.aboutToShow.connect(lambda: self.preflight_action.setEnabled(self.clusters.currentWidget() is not None))
        self.unused_action = QAction("Unused objects…", self)
        self.unused_action.setToolTip("ConfigMaps, Secrets and claims that no pod, workload, Ingress or Service Account refers to")
        self.unused_action.triggered.connect(self.show_unused)
        view_menu.addAction(self.unused_action)
        view_menu.aboutToShow.connect(lambda: self.unused_action.setEnabled(self.clusters.currentWidget() is not None))
        self.health_action = QAction("Health check…", self)
        self.health_action.setToolTip("Problems and risks in the namespace in front: crash loops, no limits or probes, latest tags, unbound claims")
        self.health_action.triggered.connect(self.show_health)
        view_menu.addAction(self.health_action)
        view_menu.aboutToShow.connect(lambda: self.health_action.setEnabled(self.clusters.currentWidget() is not None))
        self.capacity_action = QAction("Capacity…", self)
        self.capacity_action.setToolTip("Requests, limits and usage against what each node can give, per node and per namespace")
        self.capacity_action.triggered.connect(self.show_capacity)
        view_menu.addAction(self.capacity_action)
        view_menu.aboutToShow.connect(lambda: self.capacity_action.setEnabled(self.clusters.currentWidget() is not None))
        view_menu.aboutToShow.connect(lambda: self.permissions_action.setEnabled(self.clusters.currentWidget() is not None))
        view_menu.aboutToShow.connect(lambda: self.search_action.setEnabled(self.clusters.currentWidget() is not None))
        self.read_only_action = QAction("Read-only mode", self, checkable=True)
        self.read_only_action.setToolTip("Refuse every change to the cluster in front (useful for production); kept for next time")
        self.read_only_action.triggered.connect(self.set_read_only)
        view_menu.addAction(self.read_only_action)
        view_menu.aboutToShow.connect(self._sync_read_only_action)
        self.big_action = QAction("Big cluster mode", self, checkable=True)
        self.big_action.setToolTip("Lighter reads for a cluster with many nodes and pods (Warning events only on the Overview, bigger "
                                   "pages, parallel reads, slower automatic reloads). Found automatically; this is your own choice.")
        self.big_action.triggered.connect(self.set_big_mode)
        view_menu.addAction(self.big_action)
        view_menu.aboutToShow.connect(self._sync_big_action)
        # Plugins: shown only while an enabled plugin has settings for each cluster; its entries need a cluster in front
        self.plugins_menu = self.menuBar().addMenu("Plugins")
        self.plugins_menu.aboutToShow.connect(self._fill_plugins_menu)
        self.plugins.changed.connect(self._update_plugins_menu)
        self.about_action = QAction("About Klotse", self)
        self.about_action.setMenuRole(QAction.AboutRole)
        self.about_action.triggered.connect(self.show_about)
        self.menuBar().addMenu("Help").addAction(self.about_action)

        self.clusters = QTabWidget()
        self.clusters.setTabsClosable(True)
        self.clusters.tabCloseRequested.connect(self._close_cluster_tab)
        self.clusters.currentChanged.connect(self._on_cluster_changed)
        self.setCentralWidget(self.clusters)

        self.nav = Navigator()
        nav_dock = QDockWidget("Clusters", self)
        nav_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        nav_dock.setWidget(self.nav)
        nav_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.LeftDockWidgetArea, nav_dock)
        self.nav.context_clicked.connect(self.open_cluster)
        self.nav.connect_requested.connect(self.open_cluster)
        self.nav.disconnect_requested.connect(self._discard)
        self.nav.reconnect_requested.connect(self.reconnect_cluster)
        self.nav.view_requested.connect(self._open_view)
        self.nav.plugin_entries = lambda: [(p.id, f"{p.name or p.id}: settings for this cluster…")
                                           for p in self.plugins.cluster_plugins()]
        self.nav.plugin_settings_requested.connect(self.open_cluster_settings)

        self.status = self.statusBar()
        # Nothing connects by itself: the kubeconfig's current-context is not opened any more. Clusters the user asked
        # to remember when exiting come back through restore_clusters(), which the entry point calls.
        self.contexts, _ = self.new_service().load_contexts()
        self.nav.set_contexts(self.contexts, self.groups)
        self._update_plugins_menu()
        self.nav.groups_changed.connect(self._save_groups)
        self.status.showMessage("Double-click a cluster on the left to connect" if self.contexts
                                else self._no_contexts_message())

    def _save_groups(self):
        try:
            self.groups.save()
        except OSError as error:
            QMessageBox.warning(self, "Cannot save the cluster groups", str(error))

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

    def open_cluster(self, ctx):
        """Open `ctx` in its own tab, or focus it if it is already open."""
        if ctx in self.workspaces:
            self.clusters.setCurrentWidget(self.workspaces[ctx])
            return
        ws = ClusterWorkspace(self.new_service(), ctx, label_settings=self.label_settings,
                              terminal_settings=self.terminal_settings, plugins=self.plugins, audit_log=self.audit_log)
        self.workspaces[ctx] = ws
        ws.detail.set_show_managed_fields(self.managed_fields_action.isChecked())
        self.managed_fields_action.toggled.connect(ws.detail.set_show_managed_fields)
        ws.message.connect(self.status.showMessage)
        ws.state.connect(lambda s, c=ctx: self._on_state(c, s))
        ws.failed.connect(lambda m, c=ctx: self._on_failed(c, m))
        ws.view_opened.connect(lambda key, c=ctx: self._on_view_opened(c, key))
        ws.karpenter_found.connect(lambda entries, c=ctx: self.nav.show_karpenter(c, entries))
        ws.gateway_found.connect(lambda entries, c=ctx: self.nav.show_gateway(c, entries))
        ws.crd_groups_found.connect(lambda entries, c=ctx: self.nav.show_crd_groups(c, entries))
        ws.customize_requested.connect(self.customize_labels)  # the Columns… button on Nodes / Pods tabs
        self.clusters.setCurrentIndex(self.clusters.addTab(ws, ctx))
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
        self.clusters.setTabText(self.clusters.indexOf(ws), ws.context + marks)

    def _found_big(self, ws, nodes, pods):
        """The Overview showed a big cluster. Unless the user chose before, remember it, switch the lighter mode on and say so."""
        try:
            news = self.big_clusters.detected(ws.context)
        except OSError:
            news = True  # not saved, but still used for this session
        if news:
            ws.set_big(True)
            self._maybe_big_notice(ws)
            self.status.showMessage(f"{ws.context}: {nodes} nodes, {pods} pods, so big-cluster mode is on (View > Big cluster mode). "
                                    "The Overview now shows Warning events only and reloads less often.", 15000)

    def _maybe_big_notice(self, ws):
        """Tell the user, once per connection, that a big cluster is read in a lighter way and what that changes, unless they ticked
        "Don't show this again" for this cluster. Not modal (the window stays usable); nothing appears in a window that is not shown."""
        if ws is None or not ws.big or not self.isVisible() or self.big_clusters.notice_hidden(ws.context):
            return
        shown = self.__dict__.setdefault("_big_notices", {})
        if ws.context in shown:
            return
        box = QMessageBox(QMessageBox.Information, f"{ws.context}: big cluster", NOTICE_TEXT, QMessageBox.Ok, self)
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

    # ---- Plugins menu ---------------------------------------------------
    def _update_plugins_menu(self):
        self.plugins_menu.menuAction().setVisible(bool(self.plugins.cluster_plugins()))

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
                    entries.append(Entry("Go to", label, f"{group} · {ws.context}" if group else ws.context, lambda k=key, w=ws: self._goto_view(w, k)))
            for name in getattr(ws, "_ns_names", []):
                if name != "All namespaces":
                    entries.append(Entry("Namespace", name, ws.context, lambda n=name, w=ws: w.set_namespace(n)))
            entries.append(Entry("Namespace", "All namespaces", ws.context, lambda w=ws: w.set_namespace("All namespaces")))
        for ctx in self.contexts:
            if ctx in self.workspaces:
                entries.append(Entry("Cluster", ctx, "switch to it", lambda c=ctx: self.clusters.setCurrentWidget(self.workspaces[c])))
            else:
                entries.append(Entry("Cluster", ctx, "connect", lambda c=ctx: self.open_cluster(c)))
        for menu_action in self.menuBar().actions():
            menu = menu_action.menu()
            if menu is None:
                continue
            menu.aboutToShow.emit()  # menus enable their entries lazily, when they are about to show
            title = menu_action.text().replace("&", "")
            for action in menu.actions():
                text = action.text().replace("&", "").replace("…", "").strip()
                if action.isSeparator() or not text or action is self.palette_action or action.menu() is not None or not action.isEnabled():
                    continue
                entries.append(Entry("Action", text, f"{title} menu" + (" · checked" if action.isCheckable() and action.isChecked() else ""), action.trigger))
        return entries

    def _goto_view(self, ws, key):
        self.clusters.setCurrentWidget(ws)
        ws.open_view(key)

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

    def open_preferences(self):
        # One Preferences window for everything: General, Terminal and Plugins. macOS shows the entry in the application menu (Settings/Preferences, Cmd-,), elsewhere it is Edit.
        dialog = PreferencesDialog(self.kubeconfig_settings, self.config_folder(), self,
                                   terminal=self.terminal_settings,
                                   plugins=self.plugins, themes=self.theme_settings, debug=self.debug_settings)
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
        for ctx in list(self.workspaces):
            self._discard(ctx)
        contexts, _ = self.new_service().load_contexts()
        self.contexts = contexts
        self.nav.set_contexts(contexts, self.groups)
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
            self.nav.show_views(ctx)
            QTimer.singleShot(0, lambda c=ctx: self._maybe_big_notice(self.workspaces.get(c)))

    def _on_failed(self, ctx, msg):
        self._discard(ctx)
        self.nav.set_state(ctx, "failed")  # after _discard, which resets the dot to idle
        self.status.showMessage(f"{ctx}: {msg}")
        QMessageBox.warning(self, "Cannot connect", f"{ctx}\n\n{msg}")

    def _close_cluster_tab(self, index):
        ws = self.clusters.widget(index)
        self._discard(ws.context)

    def _discard(self, ctx):
        ws = self.workspaces.pop(ctx, None)
        if ws is None:
            return
        self.clusters.removeTab(self.clusters.indexOf(ws))
        ws.shutdown()
        ws.deleteLater()
        self.nav.hide_views(ctx)
        self.nav.set_state(ctx, "idle")

    def _open_view(self, ctx, key):
        ws = self.workspaces.get(ctx)
        if ws is None:
            return
        self.clusters.setCurrentWidget(ws)
        ws.open_view(key)

    def _on_view_opened(self, ctx, key):
        if self.clusters.currentWidget() is self.workspaces.get(ctx):
            self.nav.select_view(ctx, key)

    def _on_cluster_changed(self, index):
        ws = self.clusters.widget(index)
        self.setWindowTitle(f"Klotse — {ws.context}" if ws else "Klotse")
        if ws is None:
            return
        if ws.tabs.count():
            self.nav.select_view(ws.context, ws.tabs.currentWidget().view_key)
        else:
            self.nav.select_cluster(ws.context)

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
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
