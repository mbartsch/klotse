import sys
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget, QMainWindow, QMessageBox, QSizePolicy,
                               QTabWidget, QWidget)

from .about import AboutDialog
from .cluster_settings_dialog import ClusterSettingsDialog
from .cluster_groups import ClusterGroups
from .cluster_session import ClusterSession
from ..plugins.manager import PluginManager
from .customizations import ANNOTATION_KINDS, LabelColumnsDialog, LabelSettings
from .edit_menu import EditActions
from .log_view import shutdown_all
from .navigator import Navigator
from .preferences import KubeconfigSettings, PreferencesDialog
from . import theme
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
        self.kubeconfig_settings = KubeconfigSettings(self.label_settings.store, parent=self)
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
        self.menuBar().addMenu("File").addAction(self.exit_action)
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
        self.menuBar().addMenu("View").addAction(self.managed_fields_action)
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
        for ctx in wanted:
            self.open_cluster(ctx)
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
                              terminal_settings=self.terminal_settings, plugins=self.plugins)
        self.workspaces[ctx] = ws
        ws.detail.set_show_managed_fields(self.managed_fields_action.isChecked())
        self.managed_fields_action.toggled.connect(ws.detail.set_show_managed_fields)
        ws.message.connect(self.status.showMessage)
        ws.state.connect(lambda s, c=ctx: self._on_state(c, s))
        ws.failed.connect(lambda m, c=ctx: self._on_failed(c, m))
        ws.view_opened.connect(lambda key, c=ctx: self._on_view_opened(c, key))
        ws.customize_requested.connect(self.customize_labels)  # the Columns… button on Nodes / Pods tabs
        self.clusters.setCurrentIndex(self.clusters.addTab(ws, ctx))
        ws.start()

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
        # One Preferences window for everything: General, Terminal (macOS only: the only terminal choice there is) and
        # Plugins. macOS shows the entry in the application menu (Settings/Preferences, Cmd-,), elsewhere it is Edit.
        dialog = PreferencesDialog(self.kubeconfig_settings, self.config_folder(), self,
                                   terminal=self.terminal_settings if sys.platform == "darwin" else None,
                                   plugins=self.plugins, themes=self.theme_settings)
        accepted = dialog.exec() == PreferencesDialog.Accepted
        chosen = dialog.chosen_kubeconfig()
        chosen_theme = dialog.chosen_theme()
        dialog.deleteLater()
        if accepted and chosen_theme and chosen_theme != self.theme_settings.selected().name:
            self.apply_theme(chosen_theme)
        if accepted and chosen != self.kubeconfig_settings.path:
            self.apply_kubeconfig(chosen)

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
                self.session.remember(opened, front.context if front is not None else None)
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
