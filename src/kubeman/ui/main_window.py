from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QMainWindow, QMessageBox, QSizePolicy,
                               QTabWidget, QToolBar, QToolButton, QWidget)

from .customizations import LabelColumnsDialog, LabelSettings
from .log_view import shutdown_all
from .navigator import Navigator
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
        self.setWindowTitle("Kubeman")
        self.resize(1500, 850)
        self._shutting_down = False
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence.Quit)
        self.exit_action.setMenuRole(QAction.QuitRole)
        self.exit_action.triggered.connect(self.close)
        self.menuBar().addMenu("File").addAction(self.exit_action)
        self.customizations_menu = self.menuBar().addMenu("Customizations")
        # one entry per kind, shown only while a list of that kind is in front (see _update_customizations)
        self.customization_actions = {}
        for kind in ("Node", "Pod"):
            self.customization_actions[kind] = self.customizations_menu.addAction(
                f"{kind} label columns…", lambda checked=False, k=kind: self.customize_labels(k))
        self.no_customizations = self.customizations_menu.addAction("Nothing to customize in this view")
        self.no_customizations.setEnabled(False)
        self.customizations_menu.aboutToShow.connect(self._update_customizations)
        self._update_customizations()
        self.exit_toolbar = QToolBar("Application", self)
        self.exit_toolbar.setMovable(False)
        self.exit_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.customizations_button = QToolButton(text="Customizations")
        self.customizations_button.setPopupMode(QToolButton.InstantPopup)
        self.customizations_button.setMenu(self.customizations_menu)
        self.exit_toolbar.addWidget(self.customizations_button)
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
        contexts, active = self.factory().load_contexts()
        self.nav.set_contexts(contexts)
        if not contexts:
            self.status.showMessage("No kubeconfig contexts found")
        else:
            self.open_cluster(active if active in contexts else contexts[0])

    # ---- clusters ------------------------------------------------------
    def workspace(self, context=None):
        """The workspace for `context`, or the one in the current tab."""
        return self.workspaces.get(context) if context else self.clusters.currentWidget()

    def open_cluster(self, ctx):
        """Open `ctx` in its own tab, or focus it if it is already open."""
        if ctx in self.workspaces:
            self.clusters.setCurrentWidget(self.workspaces[ctx])
            return
        ws = ClusterWorkspace(self.factory(), ctx, label_settings=self.label_settings)
        self.workspaces[ctx] = ws
        ws.message.connect(self.status.showMessage)
        ws.state.connect(lambda s, c=ctx: self._on_state(c, s))
        ws.failed.connect(lambda m, c=ctx: self._on_failed(c, m))
        ws.view_opened.connect(lambda key, c=ctx: self._on_view_opened(c, key))
        ws.view_opened.connect(lambda _key: self._update_customizations())
        self.clusters.setCurrentIndex(self.clusters.addTab(ws, ctx))
        ws.start()

    def _update_customizations(self):
        """Offer Pod columns on a pod list and Node columns on the Nodes list; nothing elsewhere."""
        ws = self.clusters.currentWidget() if hasattr(self, "clusters") else None
        kind = ws.foreground_kind() if ws is not None else None
        for k, action in self.customization_actions.items():
            action.setVisible(k == kind)
        self.no_customizations.setVisible(kind not in self.customization_actions)

    def customize_labels(self, kind):
        available = set()
        for workspace in self.workspaces.values():
            for table in workspace.resource_tables():
                if table.kind == kind:
                    for row in table.model.rows:
                        metadata = (row.raw or {}).get("metadata") or {}
                        available.update((metadata.get("labels") or {}).keys())
        dialog = LabelColumnsDialog(kind, self.label_settings, available, self)
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
