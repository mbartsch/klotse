"""The optional fleet-level overview for open clusters."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme


class ClusterOverview(QWidget):
    """A compact, read-only operations table backed by the open workspaces."""

    open_requested = Signal(str)
    refresh_requested = Signal(str)
    refresh_all_requested = Signal()

    HEADERS = ("Cluster", "State", "Nodes ready / total", "Pods running / pending", "Issues", "Updated")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clusterOverview")
        self.setAccessibleName("All clusters overview")
        self._workspaces = {}
        self._names = {}
        self._rows = {}
        self._states = {}
        self._updated = {}
        self._hooks = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel("All clusters")
        title.setObjectName("pageTitle")
        title_font = title.font()
        title_font.setPointSizeF(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title_box.addWidget(title)
        self.subtitle = QLabel("A live operating view of every connected cluster")
        self.subtitle.setStyleSheet(f"color: {theme.MUTED};")
        title_box.addWidget(self.subtitle)
        heading.addLayout(title_box)
        heading.addStretch(1)
        self.refresh_button = QPushButton("Refresh all")
        self.refresh_button.setToolTip("Refresh the overview for every connected cluster")
        self.refresh_button.clicked.connect(self.refresh_all_requested)
        heading.addWidget(self.refresh_button)
        root.addLayout(heading)

        self.summary = QLabel()
        self.summary.setObjectName("fleetSummary")
        self.summary.setStyleSheet(f"color: {theme.MUTED}; padding: 8px 0;")
        root.addWidget(self.summary)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter connected clusters…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Filter all clusters")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setObjectName("clusterOverviewTable")
        self.table.setAccessibleName("Connected cluster summary")
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setMinimumSectionSize(100)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._open_row(row))
        root.addWidget(self.table, 1)

        self.detail = QFrame()
        self.detail.setObjectName("clusterOverviewDetail")
        self.detail.setStyleSheet(f"QFrame#clusterOverviewDetail {{ background: {theme.BASE}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}")
        detail_layout = QGridLayout(self.detail)
        detail_layout.setContentsMargins(14, 10, 14, 10)
        detail_layout.setHorizontalSpacing(20)
        self.detail_title = QLabel("Select a cluster")
        self.detail_title.setStyleSheet(f"font-weight: 600; color: {theme.TEXT};")
        self.detail_text = QLabel("Choose a row to see its current totals and actions.")
        self.detail_text.setStyleSheet(f"color: {theme.MUTED};")
        self.detail_text.setWordWrap(True)
        self.open_button = QPushButton("Open cluster")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_selected)
        self.refresh_cluster_button = QPushButton("Refresh cluster")
        self.refresh_cluster_button.setEnabled(False)
        self.refresh_cluster_button.clicked.connect(self._refresh_selected)
        detail_layout.addWidget(self.detail_title, 0, 0)
        detail_layout.addWidget(self.detail_text, 1, 0)
        detail_layout.addWidget(self.open_button, 0, 1, 2, 1)
        detail_layout.addWidget(self.refresh_cluster_button, 0, 2, 2, 1)
        detail_layout.setColumnStretch(0, 1)
        root.addWidget(self.detail)

        self._apply_table_style()
        self._set_summary()

    def _apply_table_style(self):
        self.table.setStyleSheet(
            f"QTableWidget {{ border: 1px solid {theme.BORDER}; background: {theme.BASE}; gridline-color: {theme.BORDER}; }}"
            f" QTableWidget::item {{ padding: 7px; }}"
            f" QTableWidget::item:selected {{ background: {theme.SELECTED_ROW}; color: {theme.TEXT}; }}"
            f" QHeaderView::section {{ background: {theme.BG}; color: {theme.MUTED}; border: 0; border-bottom: 1px solid {theme.BORDER}; padding: 8px; }}"
        )

    def set_workspaces(self, workspaces, names=None):
        self._workspaces = dict(workspaces)
        self._names = dict(names or {})
        for context, workspace in self._workspaces.items():
            if workspace not in self._hooks:
                workspace.overview_updated.connect(lambda data, c=context: self.set_cluster_data(c, data))
                workspace.state.connect(lambda state, c=context: self.set_cluster_state(c, state))
                self._hooks.add(workspace)
            data = getattr(getattr(workspace, "overview", None), "_data", None)
            if data is not None:
                self.set_cluster_data(context, data)
            elif context not in self._states:
                self._states[context] = "Loading"
        for context in list(self._states):
            if context not in self._workspaces:
                self._states.pop(context, None)
                self._rows.pop(context, None)
                self._updated.pop(context, None)
        self._rebuild()

    def set_cluster_state(self, context, state):
        self._states[context] = {"connected": "Healthy", "connecting": "Loading", "failed": "Offline"}.get(state, state.title())
        self._rebuild()

    def set_cluster_data(self, context, data):
        counts = (data or {}).get("counts") or {}
        self._rows[context] = {
            "nodes_ready": int(counts.get("nodes_ready", 0) or 0),
            "nodes": int(counts.get("nodes", 0) or 0),
            "running": int(counts.get("running", 0) or 0),
            "pending": int(counts.get("pending", 0) or 0),
            "failed": int(counts.get("failed", 0) or 0),
            "warnings": int(counts.get("warnings", 0) or 0),
        }
        values = self._rows[context]
        attention = values["failed"] or values["warnings"] or values["pending"] or values["nodes_ready"] < values["nodes"]
        self._states[context] = "Needs attention" if attention else "Healthy"
        self._updated[context] = datetime.now().strftime("%H:%M:%S")
        self._rebuild()

    def _rebuild(self):
        selected = self._selected_context()
        self.table.setRowCount(0)
        for context in self._workspaces:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = self._rows.get(context, {})
            name = self._names.get(context, context)
            state = self._states.get(context, "Loading")
            issues = values.get("failed", 0) + values.get("warnings", 0)
            cells = (name, state,
                     f"{values.get('nodes_ready', 0)} / {values.get('nodes', 0)}" if values else "—",
                     f"{values.get('running', 0)} / {values.get('pending', 0)}" if values else "—",
                     str(issues) if values else "—", self._updated.get(context, "—"))
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, context)
                if column == 1:
                    item.setForeground(QColor(theme.OK if state == "Healthy" else theme.WARN if state in ("Loading", "Needs attention") else theme.BAD))
                self.table.setItem(row, column, item)
        self._filter(self.search.text())
        if selected:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.UserRole) == selected:
                    self.table.selectRow(row)
                    break
        self._set_summary()

    def _set_summary(self):
        healthy = sum(self._states.get(c) == "Healthy" for c in self._workspaces)
        attention = sum(self._states.get(c) == "Needs attention" for c in self._workspaces)
        offline = sum(self._states.get(c) == "Offline" for c in self._workspaces)
        nodes = sum(v.get("nodes", 0) for v in self._rows.values())
        pods = sum(v.get("running", 0) + v.get("pending", 0) + v.get("failed", 0) for v in self._rows.values())
        self.summary.setText(f"{len(self._workspaces)} connected  ·  {healthy} healthy  ·  {attention} needs attention  ·  {offline} offline  ·  {nodes:,} nodes  ·  {pods:,} pods")

    def _filter(self, text):
        needle = (text or "").strip().casefold()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().casefold()
            self.table.setRowHidden(row, bool(needle and needle not in name))

    def _selected_context(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _show_detail(self):
        context = self._selected_context()
        if not context:
            self.detail_title.setText("Select a cluster")
            self.detail_text.setText("Choose a row to see its current totals and actions.")
            self.open_button.setEnabled(False)
            self.refresh_cluster_button.setEnabled(False)
            return
        values = self._rows.get(context, {})
        name = self._names.get(context, context)
        self.detail_title.setText(name)
        self.detail_text.setText(f"{values.get('nodes_ready', 0)} of {values.get('nodes', 0)} nodes ready  ·  "
                                 f"{values.get('running', 0)} running / {values.get('pending', 0)} pending pods  ·  "
                                 f"{values.get('failed', 0)} failed pods  ·  {values.get('warnings', 0)} warning events")
        self.open_button.setEnabled(context in self._workspaces)
        self.refresh_cluster_button.setEnabled(context in self._workspaces)

    def _open_row(self, row):
        context = self.table.item(row, 0).data(Qt.UserRole)
        if context:
            self.open_requested.emit(context)

    def _open_selected(self):
        context = self._selected_context()
        if context:
            self.open_requested.emit(context)

    def _refresh_selected(self):
        context = self._selected_context()
        if context:
            self.refresh_requested.emit(context)
