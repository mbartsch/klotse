import sys

from PySide6.QtCore import QElapsedTimer, QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QBoxLayout, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
                               QHeaderView, QMenu, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from ..k8s.resources import SPEC_BY_KEY
from .loading_pane import LoadingPane
from .resource_table import ResourceTable
from .action_icons import action_icon
from . import theme

# (counts key, caption, tone when the value is non-zero)
CLUSTER_CARDS = [("nodes", "Nodes", ""), ("nodes_ready", "Nodes ready", ""), ("namespaces", "Namespaces", ""),
                 ("cpu", "CPU allocatable", ""), ("memory", "Memory allocatable", ""),
                 ("pods", "Pods", ""), ("running", "Running", ""), ("pending", "Pending", "warn"),
                 ("failed", "Failed", "bad"), ("warnings", "Warning events", "warn")]
WORKLOAD_CARDS = [("pods", "Pods", ""), ("running", "Running", ""), ("pending", "Pending", "warn"),
                  ("failed", "Failed", "bad"), ("warnings", "Warning events", "warn"),
                  ("deployments", "Deployments", ""), ("daemonsets", "DaemonSets", ""),
                  ("statefulsets", "StatefulSets", ""), ("replicasets", "ReplicaSets", ""),
                  ("jobs", "Jobs", ""), ("cronjobs", "CronJobs", "")]
def metric_format(fmt):
    """Formatter for a usage column: blank when metrics-server gave no value (stored as a negative number)."""
    return lambda v: fmt(v) if v >= 0 else ""


def set_format(fmt):
    """Formatter for a request/limit column: blank when not set (stored as 0)."""
    return lambda v: fmt(v) if v > 0 else ""


_milli = lambda v: f"{v * 1000:.0f}"  # noqa: E731
_mib = lambda v: f"{v:.0f}"  # noqa: E731
_pct = lambda v: f"{v:.0f}%"  # noqa: E731

RESOURCE_COLUMNS = {  # request/limit columns: hidden by default (see customizations.DEFAULT_HIDDEN)
    "cpu_req", "cpu_lim", "cpu_req_pct", "cpu_lim_pct", "mem_req", "mem_lim", "mem_req_pct", "mem_lim_pct"}
# the Overview drill-down tables keep the compact set
POD_COLUMNS_BASIC = [("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"), ("Status", "status"),
                     ("Restarts", "restarts"), ("CPU (m)", "cpu_used", metric_format(_milli)),
                     ("Mem (MiB)", "mem_used", metric_format(_mib)), ("Node", "node"), ("Age", "age")]
POD_COLUMNS = [("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"), ("Status", "status"),
               ("Restarts", "restarts"),
               ("CPU (m)", "cpu_used", metric_format(_milli)),
               ("CPU req (m)", "cpu_req", set_format(_milli)), ("CPU lim (m)", "cpu_lim", set_format(_milli)),
               ("CPU %req", "cpu_req_pct", metric_format(_pct)), ("CPU %lim", "cpu_lim_pct", metric_format(_pct)),
               ("Mem (MiB)", "mem_used", metric_format(_mib)),
               ("Mem req (MiB)", "mem_req", set_format(_mib)), ("Mem lim (MiB)", "mem_lim", set_format(_mib)),
               ("Mem %req", "mem_req_pct", metric_format(_pct)), ("Mem %lim", "mem_lim_pct", metric_format(_pct)),
               ("Node", "node"), ("Age", "age")]
# cards that drill down when clicked: counts key -> (title, which table shows the rows)
DRILL_DOWN = {"pending": ("Pending pods", "pods"), "failed": ("Failed pods", "pods"),
              "warnings": ("Warning events", "events")}
COLUMNS = 5


class Card(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, tone):
        super().__init__()
        self.key, self.tone = key, tone
        self._clickable = self._selected = False
        self.setFrameShape(QFrame.StyledPanel)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAccessibleName(title)
        self.value = QLabel("–")
        self.caption = QLabel(title)
        theme.muted(self.caption)
        self.detail = QLabel("")
        self.detail.setStyleSheet(f"font-size: 10px; color: {theme.MUTED}; border: 0;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 10)
        lay.setSpacing(3)
        lay.addWidget(self.caption)
        lay.addWidget(self.value)
        lay.addWidget(self.detail)
        self.set_value("–")
        self._restyle()

    def set_value(self, v):
        self.value.setText(str(v))
        self.setAccessibleDescription(f"{self.value.text()}")
        color = {"warn": theme.WARN, "bad": theme.BAD}.get(self.tone) if v not in (0, "0", "–") else None
        self.value.setStyleSheet(f"font-size: 22px; font-weight: 600;{f' color: {color};' if color else ''}")

    def set_detail(self, text):
        self.detail.setText(text)

    def set_clickable(self, on):
        self._clickable = on
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self.setFocusPolicy(Qt.StrongFocus if on else Qt.NoFocus)
        self.setToolTip("Click to list them" if on else "")
        self._restyle()

    def set_selected(self, on):
        self._selected = on
        self._restyle()

    def _restyle(self):
        border = theme.ACCENT if self._selected else theme.BORDER
        hover = f"Card:hover {{ border-color: {theme.ACCENT}; }}" if self._clickable else ""
        focus = f"Card:focus {{ border: 2px solid {theme.ACCENT_HI}; }}" if self._clickable else ""
        self.setStyleSheet(f"Card {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {theme.BASE},stop:1 {theme.BG}); border: 1px solid {border}; border-radius: 9px; }} {hover} {focus}")

    def mousePressEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self._clickable and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            event.accept()
            return
        super().keyPressEvent(event)


def bar_percent(used, total):
    return round(100 * used / total) if total > 0 else 0


def usage_color_key(fraction, base="ACCENT_HI"):
    """The colour token for a usage bar that is `fraction` full: the base colour, amber above 75%, red above 90%
    (the theme's warning and failing colours, so the thresholds read as status)."""
    if fraction > 0.9:
        return "BAD"
    if fraction > 0.75:
        return "WARN"
    return base


class UsageBar(QWidget):
    """One compact bar showing observed use against allocatable cluster capacity. With `thresholds`, the colour follows how full it is."""

    def __init__(self, caption, color_key="ACCENT", thresholds=False):
        super().__init__()
        self.caption, self.color_key, self.thresholds = caption, color_key, thresholds
        self.used, self.total, self.text = 0.0, 0.0, ""
        self.setMinimumHeight(34)

    @property
    def fraction(self):
        return min(self.used / self.total, 1.0) if self.total > 0 else 0.0

    def set_usage(self, used, total, text):
        self.used, self.total, self.text = used, total, text
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(theme.MUTED))
        p.drawText(0, 12, f"{self.caption}  {self.text}")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(QRectF(0, 18, self.width(), 10), 5, 5)
        fraction = self.fraction
        key = usage_color_key(fraction, self.color_key) if self.thresholds else self.color_key
        p.setBrush(QColor(getattr(theme, key)))
        p.drawRoundedRect(QRectF(0, 18, self.width() * fraction, 10), 5, 5)


class OverviewView(QWidget):
    """Cluster dashboard and the existing workload summary/drill-down view."""

    node_selected = Signal(object)
    node_open_requested = Signal(str)
    namespace_open_requested = Signal(str)
    action_requested = Signal(str)

    def __init__(self, cards, context=""):
        super().__init__()
        self.is_cluster_overview = any(key == "nodes" for key, _title, _tone in cards)
        self.context = context
        self._panel_frames = []
        self.cards = {}
        self._data = None
        self.focus = None
        self.loading_page = LoadingPane()
        self.loading_label, self.loading_clock = self.loading_page.label, self.loading_page.clock
        self._load_clock = QElapsedTimer()
        self._load_timer = QTimer(self, interval=1000)
        self._load_timer.timeout.connect(self._loading_tick)
        self.plugin_cards = {}
        self.plugin_grid = QGridLayout()
        self.title = QLabel("Recent events", self)
        self.title.setAccessibleName("Overview table heading")
        back_label = "Show recent events" if self.is_cluster_overview else "Back to Workload health"
        back_name = "Return to recent events" if self.is_cluster_overview else "Return to Workload health"
        self.back = QPushButton(back_label)
        self.back.setAccessibleName(back_name)
        self.back.clicked.connect(lambda: self._set_focus(None))
        self.back.hide()

        for key, title, tone in cards:
            card = Card(key, title, tone)
            card.setParent(self)
            card.clicked.connect(self._on_card)
            self.cards[key] = card

        self.cpu_bar = UsageBar("CPU in use", "ACCENT_HI", thresholds=True)
        self.mem_bar = UsageBar("Memory in use", "ACCENT_HI", thresholds=True)
        self.pod_bar = UsageBar("Pods", "OK")
        self.usage = QWidget()
        usage = QHBoxLayout(self.usage)
        usage.setContentsMargins(0, 0, 0, 0)
        usage.setSpacing(10)
        usage.addWidget(self.cpu_bar)
        usage.addWidget(self.mem_bar)
        usage.addWidget(self.pod_bar)
        self.usage.hide()

        event_columns = [("Object", "object"), ("Namespace", "namespace"), ("Reason", "reason"),
                         ("Age", "last_seen"), ("Message", "message")]
        self.events_table = ResourceTable(event_columns, "Event", editable=False, customizable=False)
        self._event_dialogs = {}
        self._recent_event_columns_sized = False
        self.events_table.view.activated.connect(self._open_event_details)
        self.events_table.view.doubleClicked.connect(self._open_event_details)
        if sys.platform == "darwin":  # Qt activates a row with Cmd+O there and treats Return as "edit", so Return would do nothing
            self.events_table.view.installEventFilter(self)
        if self.is_cluster_overview:
            self.events_table.fit_button.hide()
            self.events_table.layout().setContentsMargins(0, 0, 0, 0)
            self.events_table.layout().setSpacing(4)
            self.events_table.filter.hide()
            self.events_table.filter_note.hide()
            self.events_table.status.hide()
            self.events_table.selection_count.hide()
            self.events_table.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.events_table.view.setAccessibleName("Recent cluster events")
            self.events_table.view.setWordWrap(True)
            self.events_table.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.events_table.view.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
            self.events_table.view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.events_table.view.verticalHeader().setMaximumSectionSize(76)
            self.events_table.view.setMinimumWidth(0)
            self.events_table.loading_page.label.setStyleSheet(f"font-size: 13px; color: {theme.MUTED};")
        self.pods_table = ResourceTable(POD_COLUMNS_BASIC, "Pod", customizable=False)
        self.warnings_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False, customizable=False)
        self.stack = QStackedWidget()
        for table in self.tables():
            self.stack.addWidget(table)
        self.stack.addWidget(self.loading_page)

        if self.is_cluster_overview:
            self._build_cluster_dashboard()
            for key, card in self.cards.items():
                if key not in ("nodes", "pods", "namespaces", "warnings"):
                    card.hide()  # retained for drill-down state, but not part of the dashboard grid
        else:
            self._build_workload_overview()

    def _panel(self, title, subtitle=""):
        frame = QFrame()
        frame.setObjectName("overviewPanel")
        self._panel_frames.append(frame)
        self._style_panel(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-size: 13px; font-weight: 650; border: 0;")
        label.setAccessibleName(title)
        header.addWidget(label)
        if subtitle:
            hint = QLabel(subtitle)
            theme.muted(hint)
            hint.setStyleSheet(f"color: {theme.MUTED}; border: 0;")
            header.addWidget(hint)
        header.addStretch(1)
        layout.addLayout(header)
        return frame, layout

    @staticmethod
    def _style_panel(frame):
        frame.setStyleSheet(f"QFrame#overviewPanel {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {theme.BASE},stop:1 {theme.BG}); border: 1px solid {theme.BORDER}; border-radius: 10px; }}")

    def _build_cluster_dashboard(self):
        self.setAccessibleName(f"Cluster overview for {self.context}" if self.context else "Cluster overview dashboard")
        self.page_title = QLabel("Cluster overview")
        self.page_title.setStyleSheet("font-size: 23px; font-weight: 650; letter-spacing: -0.4px;")
        self.context_label = QLabel(f"{self.context}  ·  Live cluster summary")
        theme.muted(self.context_label)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(self.page_title)
        heading.addWidget(self.context_label)
        self.dashboard_button = QPushButton("Dashboard")
        self.dashboard_button.setCheckable(True)
        self.dashboard_button.setChecked(True)
        self.dashboard_button.setAccessibleName("Cluster dashboard view")
        self.workloads_button = QPushButton("Workloads")
        self.workloads_button.setCheckable(True)
        self.workloads_button.setAccessibleName("Open workload overview")
        self.workloads_button.clicked.connect(lambda: self.action_requested.emit("workloads"))
        self.overview_switch = QWidget()
        switch_layout = QHBoxLayout(self.overview_switch)
        switch_layout.setContentsMargins(3, 3, 3, 3)
        switch_layout.setSpacing(2)
        for button in (self.dashboard_button, self.workloads_button):
            button.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; color: {theme.MUTED}; padding: 6px 10px; }} QPushButton:checked {{ background: {theme.BORDER}; color: {theme.TEXT}; }}")
            switch_layout.addWidget(button)
        self.quick_actions_button = QToolButton()
        self.quick_actions_button.setText("More")
        self.quick_actions_button.setPopupMode(QToolButton.InstantPopup)
        self.quick_actions_button.setAccessibleName("More cluster actions")
        menu = QMenu(self.quick_actions_button)
        for label, action in (("Node capacity…", "capacity"), ("Health check…", "health"),
                              ("My permissions…", "permissions")):
            menu.addAction(label, lambda key=action: self.action_requested.emit(key))
        self.quick_actions_button.setMenu(menu)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh cluster overview")
        self.refresh_button.clicked.connect(lambda: self.action_requested.emit("refresh"))
        self.health_summary = QLabel("Cluster status is loading…")
        self.health_summary.setAccessibleName("Cluster health summary")
        self.health_summary.setWordWrap(False)
        self.health_summary.setMinimumHeight(40)
        self.health_summary.setStyleSheet(f"background: {theme.BASE}; border: 1px solid {theme.BORDER}; border-radius: 9px; padding: 9px 13px;")

        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(9)
        self.stats_grid.setVerticalSpacing(9)
        for column, key in enumerate(("nodes", "pods", "namespaces", "warnings")):
            if key in self.cards:
                self.stats_grid.addWidget(self.cards[key], 0, column)
        self.plugin_host = QWidget()
        self.plugin_host.setLayout(self.plugin_grid)

        capacity, capacity_layout = self._panel("Resource allocation")
        self.capacity_panel = capacity
        self.capacity_detach_button = self._detachable_panel_button(capacity, "Resource allocation")
        capacity_layout.setAlignment(Qt.AlignTop)
        self.usage.layout().setDirection(QBoxLayout.TopToBottom)
        self.usage.layout().setSpacing(4)
        capacity_layout.addWidget(self.usage)
        self.capacity_note = QLabel("Usage metrics are unavailable. Install metrics-server to see live CPU and memory use.")
        self.capacity_note.setWordWrap(True)
        theme.muted(self.capacity_note)
        capacity_layout.addWidget(self.capacity_note)

        health_panel, health_layout = self._panel("Workload health")
        self.workload_health_panel = health_panel
        self.workload_health_detach_button = self._detachable_panel_button(health_panel, "Workload health")
        self.workload_rows = {}
        health_state = QWidget()
        health_state_layout = QVBoxLayout(health_state)
        health_state_layout.setContentsMargins(0, 0, 8, 0)
        health_state_layout.setSpacing(9)
        for key, label in (("running", "Running"), ("pending", "Pending"), ("failed", "Failed")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(62)
            theme.muted(name)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setStyleSheet(f"QProgressBar {{ border: 0; border-radius: 4px; background: {theme.BORDER}; }} QProgressBar::chunk {{ border-radius: 4px; background: {theme.OK if key == 'running' else theme.WARN if key == 'pending' else theme.BAD}; }}")
            value = QLabel("–")
            value.setMinimumWidth(34)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(value)
            if key in ("pending", "failed"):
                view = QPushButton("View")
                view.setFlat(True)
                view.setAccessibleName(f"View {label.lower()} pods")
                view.clicked.connect(lambda _checked=False, k=key: self._on_card(k))
                row.addWidget(view)
            health_state_layout.addLayout(row)
            self.workload_rows[key] = (bar, value)
        self.health_foot = QLabel("Counts reflect pod phase; use Health check for container-level issues.")
        self.health_foot.setWordWrap(True)
        theme.muted(self.health_foot)
        self.health_foot.setToolTip(self.health_foot.text())
        self.health_foot.hide()

        self.namespace_table = QTableWidget(0, 3)
        self._namespace_share_bars = []
        self.namespace_table.setAccessibleName("Pod distribution by namespace")
        self.namespace_table.setHorizontalHeaderLabels(["Namespace", "Pods", "Share"])
        self.namespace_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.namespace_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.namespace_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.namespace_table.verticalHeader().hide()
        self.namespace_table.verticalHeader().setDefaultSectionSize(27)
        self.namespace_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.namespace_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.namespace_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.namespace_table.setFocusPolicy(Qt.StrongFocus)
        self.namespace_table.setMaximumHeight(82)
        self.namespace_table.setMinimumWidth(0)
        self.namespace_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.namespace_table.horizontalHeader().hide()
        self.namespace_table.itemActivated.connect(self._open_namespace_item)
        namespace_summary = QWidget()
        namespace_layout = QVBoxLayout(namespace_summary)
        namespace_layout.setContentsMargins(0, 0, 0, 0)
        namespace_layout.setSpacing(4)
        namespace_head = QHBoxLayout()
        namespace_head.addWidget(QLabel("Top namespaces"))
        namespace_head.addStretch(1)
        self.all_namespaces_button = QPushButton("All namespaces")
        self.all_namespaces_button.setFlat(True)
        self.all_namespaces_button.setAccessibleName("Open all namespaces")
        self.all_namespaces_button.clicked.connect(lambda: self.action_requested.emit("namespaces"))
        namespace_head.addWidget(self.all_namespaces_button)
        namespace_layout.addLayout(namespace_head)
        namespace_layout.addWidget(self.namespace_table)
        self.namespace_note = QLabel("Namespace pod counts will appear after cluster data loads.")
        theme.muted(self.namespace_note)
        namespace_layout.addWidget(self.namespace_note)
        self.namespace_note.setVisible(False)
        health_columns = QVBoxLayout()
        health_columns.setSpacing(8)
        health_columns.addWidget(health_state)
        health_columns.addWidget(namespace_summary)
        health_layout.addLayout(health_columns)
        top_panels = QSplitter(Qt.Horizontal)
        top_panels.setObjectName("overviewTopPanels")
        top_panels.setChildrenCollapsible(False)
        top_panels.addWidget(capacity)
        top_panels.addWidget(health_panel)

        self.nodes_table = QTableWidget(0, 5)
        self.nodes_table.setAccessibleName("Node health summary")
        self.nodes_table.setHorizontalHeaderLabels(["Node", "Status", "CPU", "Memory", "Pods"])
        self.nodes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.nodes_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.nodes_table.verticalHeader().hide()
        self.nodes_table.verticalHeader().setDefaultSectionSize(27)
        self.nodes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.nodes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nodes_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.nodes_table.setFocusPolicy(Qt.StrongFocus)
        self.nodes_table.itemSelectionChanged.connect(self._node_selection_changed)
        self.nodes_table.itemActivated.connect(self._open_node_item)
        node_panel, node_layout = self._panel("Node health", "Not-ready first, then highest CPU or memory use")
        self.node_health_panel = node_panel
        node_panel.setMaximumWidth(680)
        node_panel_head = node_layout.itemAt(0).layout()
        self.all_nodes_button = QPushButton("All nodes")
        self.all_nodes_button.setAccessibleName("Open all nodes")
        self.all_nodes_button.clicked.connect(lambda: self.action_requested.emit("nodes"))
        node_panel_head.addWidget(self.all_nodes_button)
        self.node_health_detach_button = self._detachable_panel_button(node_panel, "Node health", node_panel_head,
                                                                       node_panel_head.count() - 2)
        node_layout.addWidget(self.nodes_table)
        self.node_note = QLabel("Node health will appear after cluster data loads.")
        self.node_note.setWordWrap(True)
        theme.muted(self.node_note)
        node_layout.addWidget(self.node_note)
        self._style_overview_tables()

        recent_panel, recent_layout = self._panel("Recent events")
        self.recent_events_panel = recent_panel
        recent_header = recent_layout.itemAt(0).layout()
        recent_header.addStretch(1)
        self.recent_events_detach_button = self._detachable_panel_button(recent_panel, "Recent events", recent_header, 1)
        all_events_button = QPushButton("All events")
        all_events_button.setFlat(True)
        all_events_button.setAccessibleName("Open all events")
        all_events_button.clicked.connect(lambda: self.action_requested.emit("events"))
        recent_header.addWidget(all_events_button)
        self.back.setStyleSheet("border: 0;")
        recent_header.addWidget(self.back)
        recent_layout.addWidget(self.stack, 1)
        self.stack.setMinimumHeight(270)
        self.title.hide()  # kept as the dynamic heading for the existing drill-down state
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 20)
        content_layout.setSpacing(10)
        page_head = QHBoxLayout()
        page_head.addLayout(heading, 1)
        page_head.addWidget(self.overview_switch)
        page_head.addWidget(self.quick_actions_button)
        page_head.addWidget(self.refresh_button)
        content_layout.addLayout(page_head)
        content_layout.addWidget(self.health_summary)
        content_layout.addLayout(self.stats_grid)
        content_layout.addWidget(self.plugin_host)
        bottom_panels = QSplitter(Qt.Horizontal)
        bottom_panels.setObjectName("overviewBottomPanels")
        bottom_panels.setChildrenCollapsible(False)
        bottom_panels.addWidget(node_panel)
        bottom_panels.addWidget(recent_panel)
        self.panel_splitter = QSplitter(Qt.Vertical)
        self.panel_splitter.setObjectName("overviewPanelSplitter")
        self.panel_splitter.setChildrenCollapsible(False)
        self.panel_splitter.addWidget(top_panels)
        self.panel_splitter.addWidget(bottom_panels)
        self.top_panel_splitter = top_panels
        self.bottom_panel_splitter = bottom_panels
        content_layout.addWidget(self.panel_splitter, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setMinimumWidth(0)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def _detachable_panel_button(self, panel, title, header=None, index=None):
        if not hasattr(self, "_detached_panels"):
            self._detached_panels = {}
        if header is None:
            header = panel.layout().itemAt(0).layout()
            index = header.count() - 1
        button = QToolButton()
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setIcon(action_icon("detach"))
        button.setToolTip(f"Detach {title} panel")
        button.setAccessibleName(f"Detach {title} panel")
        header.insertWidget(index, button)
        button.clicked.connect(lambda _checked=False, p=panel, b=button, t=title: self._toggle_panel_detached(p, b, t))
        return button

    def _toggle_panel_detached(self, panel, button, title):
        state = self._detached_panels.get(id(panel))
        if state:
            self._attach_panel(panel)
            return
        splitter = panel.parentWidget()
        if not isinstance(splitter, QSplitter):
            return
        index = splitter.indexOf(panel)
        placeholder = QWidget()
        placeholder.setObjectName(f"{panel.objectName() or title.replace(' ', '')}Placeholder")
        sizes = splitter.sizes()
        splitter.replaceWidget(index, placeholder)
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(420, 260)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(8, 8, 8, 8)
        panel.setParent(dialog)
        layout.addWidget(panel)
        state = {"splitter": splitter, "index": index, "placeholder": placeholder, "sizes": sizes,
                 "dialog": dialog, "button": button, "title": title}
        self._detached_panels[id(panel)] = state
        button.setIcon(action_icon("attach"))
        button.setToolTip(f"Attach {title} panel")
        button.setAccessibleName(f"Attach {title} panel")
        dialog.finished.connect(lambda _result, p=panel: self._attach_panel(p))
        dialog.resize(760, 460)
        dialog.show()

    def _attach_panel(self, panel):
        state = self._detached_panels.pop(id(panel), None)
        if state is None:
            return
        dialog = state["dialog"]
        panel.setParent(None)
        state["splitter"].insertWidget(state["index"], panel)
        state["placeholder"].hide()
        state["placeholder"].deleteLater()
        state["splitter"].setSizes(state["sizes"])
        state["button"].setIcon(action_icon("detach"))
        state["button"].setToolTip(f"Detach {state['title']} panel")
        state["button"].setAccessibleName(f"Detach {state['title']} panel")
        dialog.close()
        dialog.deleteLater()

    def _build_workload_overview(self):
        grid = QGridLayout()
        for i, (key, _title, _tone) in enumerate(WORKLOAD_CARDS):
            if key in self.cards:
                grid.addWidget(self.cards[key], i // COLUMNS, i % COLUMNS)
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.back)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(self.plugin_grid)
        layout.addWidget(self.usage)
        layout.addLayout(head)
        layout.addWidget(self.stack, 1)
    def refresh_theme(self):
        for panel in self._panel_frames:
            self._style_panel(panel)
        for card in (*self.cards.values(), *self.plugin_cards.values()):
            card._restyle()
            card.set_value(card.value.text())
        if self.is_cluster_overview:
            switch_style = f"QPushButton {{ border: 0; background: transparent; color: {theme.MUTED}; padding: 6px 10px; }} QPushButton:checked {{ background: {theme.BORDER}; color: {theme.TEXT}; }}"
            self.dashboard_button.setStyleSheet(switch_style)
            self.workloads_button.setStyleSheet(switch_style)
            self._style_overview_tables()
            for key, (bar, _value) in self.workload_rows.items():
                color = theme.OK if key == "running" else theme.WARN if key == "pending" else theme.BAD
                bar.setStyleSheet(f"QProgressBar {{ border: 0; border-radius: 4px; background: {theme.BORDER}; }} QProgressBar::chunk {{ border-radius: 4px; background: {color}; }}")
            for bar in self._namespace_share_bars:
                bar.setStyleSheet(f"QProgressBar {{ border: 0; border-radius: 3px; background: {theme.BORDER}; color: {theme.TEXT}; text-align: center; }} QProgressBar::chunk {{ border-radius: 3px; background: {theme.ACCENT}; }}")
        if self.is_cluster_overview and self._data is not None:
            self._set_health_summary(self._data.get("counts", {}))

    def _style_overview_tables(self):
        style = f"QTableWidget {{ border: 0; background: transparent; alternate-background-color: {theme.BG}; }} QTableWidget::item {{ padding: 4px; border-bottom: 1px solid {theme.BORDER}; }} QHeaderView::section {{ background: transparent; color: {theme.MUTED}; border: 0; border-bottom: 1px solid {theme.BORDER}; padding: 4px; font-size: 10px; }}"
        self.namespace_table.setStyleSheet(style)
        self.nodes_table.setStyleSheet(style)
        for row in range(self.nodes_table.rowCount()):
            item = self.nodes_table.item(row, 1)
            if item:
                item.setForeground(QColor(theme.OK if item.text().startswith("Ready") else theme.BAD))

    def tables(self):
        return [self.events_table, self.pods_table, self.warnings_table]

    def set_loading(self):
        """Show loading only in the recent-events area until the first cluster response arrives."""
        if self._data is not None or self._load_timer.isActive():
            return
        self._load_clock.start()
        self.loading_page.show_busy("Loading cluster data…")
        self.stack.setCurrentWidget(self.loading_page)
        self._load_timer.start()

    def _loading_tick(self):
        self.loading_label.setText(f"Loading cluster data… {self._load_clock.elapsed() // 1000}s")

    def set_error(self, message):
        self._load_timer.stop()
        if self._data is None:
            self.loading_page.show_failed(f"Load failed: {message}")
            self.stack.setCurrentWidget(self.loading_page)

    def set_plugin_cards(self, cards):
        """Cards plugins add under the built-in cards; values are kept when a plugin stays enabled."""
        if [key for key, _ in cards] == list(self.plugin_cards):
            return
        old = {key: card.value.text() for key, card in self.plugin_cards.items()}
        for card in self.plugin_cards.values():
            self.plugin_grid.removeWidget(card)
            card.deleteLater()
        self.plugin_cards = {}
        for i, (key, title) in enumerate(cards):
            card = Card(key, title, "")
            card.set_value(old.get(key, "–"))
            self.plugin_cards[key] = card
            self.plugin_grid.addWidget(card, i // COLUMNS, i % COLUMNS)
        if self.is_cluster_overview:
            self.plugin_host.setVisible(bool(self.plugin_cards))

    def set_plugin_values(self, values):
        for key, card in self.plugin_cards.items():
            if key in values:
                card.set_value(str(values[key]))

    def set_data(self, data):
        self._load_timer.stop()
        self._data = data
        counts = data.get("counts", {})
        lists = data.get("lists", {})
        for key, card in self.cards.items():
            card.set_value(counts.get(key, "–"))
            card.set_clickable(key in DRILL_DOWN and bool(lists.get(key)))
            if self.is_cluster_overview:
                details = {
                    "nodes": f"{counts.get('nodes_ready', 0)} of {counts.get('nodes', 0)} ready",
                    "pods": f"{counts.get('running', 0)} running · {counts.get('pending', 0)} pending · {counts.get('failed', 0)} failed",
                    "namespaces": "Pod distribution below",
                    "warnings": "Warning events",
                }
                card.set_detail(details.get(key, ""))
        usage = counts.get("usage") or {}
        cpu_usage, mem_usage, pod_usage = usage.get("cpu"), usage.get("memory"), usage.get("pods")
        self.cpu_bar.setVisible(bool(cpu_usage))
        self.mem_bar.setVisible(bool(mem_usage))
        self.pod_bar.setVisible(bool(pod_usage))
        self.usage.setVisible(bool(cpu_usage or mem_usage or pod_usage))
        if self.is_cluster_overview:
            self.capacity_note.setVisible(not (cpu_usage and mem_usage))
            visible_bars = sum(bool(value) for value in (cpu_usage, mem_usage, pod_usage))
            self.usage.setFixedHeight(41 * visible_bars + 4 * max(0, visible_bars - 1))
        for bar, values, unit, digits in ((self.cpu_bar, cpu_usage, "cores", 1),
                                          (self.mem_bar, mem_usage, "GiB", 2),
                                          (self.pod_bar, pod_usage, "slots", 0)):
            if values:
                used, total = values
                amount = f"{used:.{digits}f}" if digits else f"{int(used)}"
                capacity = f"{total:.{digits}f}" if digits else f"{int(total)}"
                bar.set_usage(used, total, f"{amount} / {capacity} {unit} ({bar_percent(used, total)}%)")
        if self.is_cluster_overview:
            self._set_health_summary(counts)
            total_pods = max(int(counts.get("pods", 0) or 0), 1)
            for key, (bar, value) in self.workload_rows.items():
                count = int(counts.get(key, 0) or 0)
                bar.setValue(round(100 * count / total_pods))
                value.setText(str(count))
            self._set_namespace_distribution(data.get("namespace_distribution"))
            self._set_nodes(data.get("nodes") or [])
        events = data.get("events") or []
        if self.is_cluster_overview:
            for row in events:
                row.message_tooltip = str(getattr(row, "message", ""))
        self.events_table.set_rows(events)
        if self.is_cluster_overview:
            self._size_recent_events()
        if self.focus and not lists.get(self.focus):
            self.focus = None
        self._render()

    def _size_recent_events(self):
        header = self.events_table.view.horizontalHeader()
        if not self._recent_event_columns_sized:
            header.setStretchLastSection(False)
            for column, width in ((0, 112), (1, 88), (2, 118), (3, 54)):
                self.events_table.view.setColumnWidth(column, width)
            header.setSectionResizeMode(4, QHeaderView.Stretch)
            self._recent_event_columns_sized = True
        self.events_table.view.resizeRowsToContents()
        self.events_table.view.clearFocus()

    def eventFilter(self, watched, event):
        """macOS: Return or Enter on the recent-events list opens the selected event, as it does on Windows and Linux."""
        if (watched is self.events_table.view and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.isAutoRepeat()):
            index = self.events_table.view.currentIndex()
            if index.isValid():
                self._open_event_details(index)
                return True
        return super().eventFilter(watched, event)

    def _open_event_details(self, index):
        if not index.isValid():
            return None
        source = self.events_table.proxy.mapToSource(index)
        if not source.isValid() or not 0 <= source.row() < len(self.events_table.model.rows):
            return None
        row = self.events_table.model.rows[source.row()]
        existing = self._event_dialogs.get(id(row))
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return existing
        dialog = QDialog(self)
        dialog.setWindowTitle("Event details")
        dialog.setAccessibleName(f"Event details for {getattr(row, 'object', '')}")
        dialog.setMinimumSize(520, 380)
        layout = QVBoxLayout(dialog)
        details = QFormLayout()
        for label, value in (("Object", getattr(row, "object", "")),
                             ("Namespace", getattr(row, "namespace", "")),
                             ("Reason", getattr(row, "reason", "")),
                             ("Age", getattr(row, "last_seen", ""))):
            field = QLabel(str(value) or "—")
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.addRow(label, field)
        layout.addLayout(details)
        message = QPlainTextEdit()
        message.setObjectName("eventFullMessage")
        message.setAccessibleName("Full event message")
        message.setReadOnly(True)
        message.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        message.setPlainText(str(getattr(row, "message", "")) or "No message was provided.")
        layout.addWidget(message, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self._event_dialogs[id(row)] = dialog
        dialog.finished.connect(lambda _result, key=id(row): self._event_dialogs.pop(key, None))
        dialog.open()
        return dialog

    def _set_health_summary(self, counts):
        nodes, ready = counts.get("nodes", 0), counts.get("nodes_ready", 0)
        pending, failed, warnings = (counts.get(key, 0) for key in ("pending", "failed", "warnings"))
        attention = failed + warnings
        if nodes and ready < nodes:
            headline = f"{nodes - ready} of {nodes} nodes need attention"
        elif attention:
            headline = f"Cluster needs attention · {failed} failed pods · {warnings} warning events"
        else:
            headline = "Cluster is healthy"
        detail = f"{ready} of {nodes} nodes ready · {pending} pods pending"
        if counts.get("by_phase"):
            detail += " · phase counts"
        self.health_summary.setText(f"{headline}   ·   {detail}")
        self.health_summary.setAccessibleDescription(f"{headline}. {detail}")
        color = theme.BAD if nodes and ready < nodes else theme.WARN if attention else theme.OK
        bg = theme.BASE if nodes and ready < nodes else theme.WARN_BG if attention else theme.BASE
        self.health_summary.setStyleSheet(f"background: {bg}; color: {color}; border: 1px solid {color}; border-radius: 9px; padding: 9px 13px; font-weight: 600;")

    def _set_namespace_distribution(self, distribution):
        self._namespace_share_bars = []
        self.namespace_table.clearContents()
        self.namespace_table.setRowCount(0)
        if distribution is None:
            self.namespace_note.setText("Distribution is unavailable in Big Cluster mode to keep refreshes light.")
            self.namespace_table.hide()
            self.namespace_note.show()
            return
        rows = sorted(distribution.items(), key=lambda item: (-item[1], item[0].casefold()))
        total = sum(count for _name, count in rows)
        shown = rows[:3]
        for row, (name, count) in enumerate(shown):
            self.namespace_table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, name)
            self.namespace_table.setItem(row, 0, item)
            self.namespace_table.setItem(row, 1, QTableWidgetItem(str(count)))
            share = QProgressBar()
            share.setMinimumWidth(0)
            share.setRange(0, max(total, 1))
            share.setValue(count)
            share.setTextVisible(True)
            share.setFormat(f"{bar_percent(count, total)}%")
            share.setAccessibleName(f"{name}: {count} of {total} pods")
            share.setStyleSheet(f"QProgressBar {{ border: 0; border-radius: 3px; background: {theme.BORDER}; color: {theme.TEXT}; text-align: center; }} QProgressBar::chunk {{ border-radius: 3px; background: {theme.ACCENT}; }}")
            self._namespace_share_bars.append(share)
            self.namespace_table.setCellWidget(row, 2, share)
        self.namespace_note.setText("No pods are currently assigned to a namespace.")
        self.namespace_note.setVisible(not rows)
        self.namespace_table.setVisible(bool(rows))

    def _set_nodes(self, nodes):
        self.nodes_table.clearContents()
        self.nodes_table.setRowCount(0)
        def priority(node):
            usage = [used / total for used, total in ((node.cpu_used, node.cpu), (node.mem_used, node.memory))
                     if used >= 0 and total > 0]
            highest = max(usage) if usage else -1
            return (0 if not node.is_ready else 1, 0 if highest >= 0 else 1, -highest if highest >= 0 else 0,
                    node.name.casefold())

        ordered = sorted(nodes, key=priority)
        shown = ordered[:8]
        self._shown_nodes = {node.name: node for node in shown}
        self.nodes_table.setRowCount(len(shown))
        for row, node in enumerate(shown):
            name_item = QTableWidgetItem(node.name)
            name_item.setData(Qt.UserRole, node)
            status_item = QTableWidgetItem(node.status)
            if node.is_ready:
                status_item.setForeground(QColor(theme.OK))
            else:
                status_item.setForeground(QColor(theme.BAD))
            self.nodes_table.setItem(row, 0, name_item)
            self.nodes_table.setItem(row, 1, status_item)
            cpu_item = QTableWidgetItem(self._node_usage_percent(node.cpu_used, node.cpu))
            cpu_item.setToolTip(self._node_usage_text(node.cpu_used, node.cpu))
            mem_item = QTableWidgetItem(self._node_usage_percent(node.mem_used, node.memory))
            mem_item.setToolTip(self._node_usage_text(node.mem_used, node.memory))
            self.nodes_table.setItem(row, 2, cpu_item)
            self.nodes_table.setItem(row, 3, mem_item)
            self.nodes_table.setItem(row, 4, QTableWidgetItem(str(node.pods) if node.pods >= 0 else "–"))
        for column, width in ((1, 56), (2, 52), (3, 57), (4, 38)):
            self.nodes_table.setColumnWidth(column, width)
        if len(ordered) > len(shown):
            self.node_note.setText(f"Showing {len(shown)} of {len(ordered)} nodes, prioritizing NotReady and high use · open All nodes to browse the full list.")
        else:
            self.node_note.setText("Select a node to inspect it in Details; press Enter to open its node view.")
        self.node_note.show()

    @staticmethod
    def _node_usage_text(used, total):
        if used < 0:
            return "Not reported"
        return f"{bar_percent(used, total)}% · {used:.2f} / {total:.2f}" if total > 0 else f"{used:.2f} used"

    @staticmethod
    def _node_usage_percent(used, total):
        return f"{bar_percent(used, total)}%" if used >= 0 and total > 0 else "–"

    def _node_selection_changed(self):
        row = self.nodes_table.currentRow()
        item = self.nodes_table.item(row, 0) if row >= 0 else None
        self.node_selected.emit(item.data(Qt.UserRole) if item else None)

    def _open_node_item(self, item):
        if item and item.column() == 0:
            self.node_open_requested.emit(item.text())

    def _open_namespace_item(self, item):
        if item and item.column() == 0:
            name = item.data(Qt.UserRole)
            if name:
                self.namespace_open_requested.emit(name)

    def _on_card(self, key):
        self._set_focus(None if self.focus == key else key)

    def _set_focus(self, key):
        self.focus = key
        self._render()

    def _render(self):
        for key, card in self.cards.items():
            card.set_selected(key == self.focus)
        self.back.setVisible(self.focus is not None)
        if self.focus is None:
            self.title.setText("Recent events")
            self.stack.setCurrentWidget(self.events_table)
            return
        title, kind = DRILL_DOWN[self.focus]
        rows = self._data["lists"][self.focus]
        table = self.pods_table if kind == "pods" else self.warnings_table
        table.set_rows(rows)
        self.title.setText(f"{title} ({len(rows)})")
        self.stack.setCurrentWidget(table)
