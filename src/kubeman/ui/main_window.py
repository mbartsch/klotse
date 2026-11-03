import sys
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QMainWindow, QMessageBox, QSizePolicy,
                               QTabWidget, QToolBar, QWidget)

from .customizations import LabelColumnsDialog, LabelSettings
from .edit_menu import EditActions
from .log_view import shutdown_all
from .navigator import Navigator
from .preferences import KubeconfigSettings, PreferencesDialog
from .terminal_settings import TerminalSettings, TerminalSettingsDialog
from .workspace import ClusterWorkspace


class MainWindow(QMainWindow):
    """Shell: the cluster navigator on the left and one workspace tab per open cluster.

    `service_factory` is called once per cluster so every workspace gets its own connection.
    """

    closing = Signal()

    def __init__(self, service_factory, label_settings=None):
        super().__init__()
        self.factory = service_factory
        self.workspaces = {}  # context name -> ClusterWorkspace
        self.label_settings = label_settings if label_settings is not None else LabelSettings(parent=self)
        self.terminal_settings = TerminalSettings(self.label_settings.store, parent=self)
        self.kubeconfig_settings = KubeconfigSettings(self.label_settings.store, parent=self)
        self.setWindowTitle("Kubeman")
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
        if sys.platform == "darwin":
            self.terminal_settings_action = QAction("Terminal…", self)
            self.terminal_settings_action.triggered.connect(self.configure_terminal)
            self.menuBar().addMenu("Settings").addAction(self.terminal_settings_action)
        self.exit_toolbar = QToolBar("Application", self)
        self.exit_toolbar.setMovable(False)
        self.exit_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.exit_toolbar.addWidget(spacer)
        self.exit_toolbar.addAction(self.exit_action)
        self.addToolBar(self.exit_toolbar)

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

        self.status = self.statusBar()
        contexts, active = self.new_service().load_contexts()
        self.nav.set_contexts(contexts)
        if not contexts:
            self.status.showMessage(self._no_contexts_message())
        else:
            self.open_cluster(active if active in contexts else contexts[0])

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
    def workspace(self, context=None):
        """The workspace for `context`, or the one in the current tab."""
        return self.workspaces.get(context) if context else self.clusters.currentWidget()

    def open_cluster(self, ctx):
        """Open `ctx` in its own tab, or focus it if it is already open."""
        if ctx in self.workspaces:
            self.clusters.setCurrentWidget(self.workspaces[ctx])
            return
        ws = ClusterWorkspace(self.new_service(), ctx, label_settings=self.label_settings,
                              terminal_settings=self.terminal_settings)
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

    def config_folder(self):
        """Folder holding Kubeman's settings file (see LabelSettings.store.fileName())."""
        return Path(self.label_settings.store.fileName()).parent

    def open_preferences(self):
        dialog = PreferencesDialog(self.kubeconfig_settings, self.config_folder(), self)
        accepted = dialog.exec() == PreferencesDialog.Accepted
        chosen = dialog.chosen_kubeconfig()
        dialog.deleteLater()
        if accepted and chosen != self.kubeconfig_settings.path:
            self.apply_kubeconfig(chosen)

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
        self.nav.set_contexts(contexts)
        self.status.showMessage(
            f"Loaded {len(contexts)} context(s) from {path or 'the default kubeconfig'}" if contexts
            else self._no_contexts_message())
        return True

    def customize_labels(self, kind):
        available, builtin = set(), ()
        for workspace in self.workspaces.values():
            for table in workspace.resource_tables():
                if table.kind == kind:
                    if table.customize_button is not None:  # not the Overview's compact drill-down tables
                        builtin = builtin or table.base_columns
                    for row in table.model.rows:
                        metadata = (row.raw or {}).get("metadata") or {}
                        available.update((metadata.get("labels") or {}).keys())
        dialog = LabelColumnsDialog(kind, self.label_settings, available, builtin, self)
        dialog.exec()
        dialog.deleteLater()

    def configure_terminal(self):
        dialog = TerminalSettingsDialog(self.terminal_settings, self)
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
        self.setWindowTitle(f"Kubeman — {ws.context}" if ws else "Kubeman")
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

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
