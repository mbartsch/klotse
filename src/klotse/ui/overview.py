from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..k8s.resources import SPEC_BY_KEY
from .loading_pane import LoadingPane
from .resource_table import ResourceTable
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
        caption = QLabel(title)
        theme.muted(caption)
        lay = QVBoxLayout(self)
        lay.addWidget(self.value)
        lay.addWidget(caption)
        self.set_value("–")
        self._restyle()

    def set_value(self, v):
        self.value.setText(str(v))
        self.setAccessibleDescription(f"{self.value.text()}")
        color = {"warn": theme.WARN, "bad": theme.BAD}.get(self.tone) if v not in (0, "0", "–") else None
        self.value.setStyleSheet(f"font-size: 22px; font-weight: 600;{f' color: {color};' if color else ''}")

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
        self.setStyleSheet(f"Card {{ border: 1px solid {border}; border-radius: 7px; }} {hover} {focus}")

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


class UsageBar(QWidget):
    """One horizontal bar: how much of a capacity is in use (green, amber above 70%, red above 90%)."""

    def __init__(self, caption):
        super().__init__()
        self.caption, self.used, self.total, self.text = caption, 0.0, 0.0, ""
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
        p.setBrush(QColor(theme.BAD if fraction > 0.9 else theme.WARN if fraction > 0.7 else theme.OK))
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
        self.title = QLabel("Recent events")
        self.title.setAccessibleName("Overview table heading")
        self.back = QPushButton("Show recent events")
        self.back.setAccessibleName("Return to recent events")
        self.back.clicked.connect(lambda: self._set_focus(None))
        self.back.hide()

        for key, title, tone in cards:
            card = Card(key, title, tone)
            card.clicked.connect(self._on_card)
            self.cards[key] = card

        self.cpu_bar, self.mem_bar = UsageBar("CPU in use"), UsageBar("Memory in use")
        self.usage = QWidget()
        usage = QHBoxLayout(self.usage)
        usage.setContentsMargins(0, 0, 0, 0)
        usage.addWidget(self.cpu_bar)
        usage.addSpacing(18)
        usage.addWidget(self.mem_bar)
        self.usage.hide()

        self.events_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False, customizable=False)
        self.pods_table = ResourceTable(POD_COLUMNS_BASIC, "Pod", customizable=False)
        self.warnings_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False, customizable=False)
        self.stack = QStackedWidget()
        for table in self.tables():
            self.stack.addWidget(table)
        self.stack.addWidget(self.loading_page)

        if self.is_cluster_overview:
            self._build_cluster_dashboard()
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
        frame.setStyleSheet(f"QFrame#overviewPanel {{ background: {theme.BASE}; border: 1px solid {theme.BORDER}; border-radius: 9px; }}")

    def _build_cluster_dashboard(self):
        self.setAccessibleName(f"Cluster overview for {self.context}" if self.context else "Cluster overview dashboard")
        self.page_title = QLabel("Cluster overview")
        self.page_title.setStyleSheet("font-size: 22px; font-weight: 650;")
        self.context_label = QLabel(self.context)
        theme.muted(self.context_label)
        page_head = QHBoxLayout()
        page_head.addWidget(self.page_title)
        page_head.addWidget(self.context_label)
        page_head.addStretch(1)
        self.health_summary = QLabel("Cluster status is loading")
        self.health_summary.setAccessibleName("Cluster health summary")
        self.health_summary.setWordWrap(True)
        self.health_summary.setStyleSheet(f"background: {theme.BASE}; border: 1px solid {theme.BORDER}; border-radius: 8px; padding: 10px 12px;")

        self.stats_grid = QGridLayout()
        stat_keys = ("nodes", "pods", "namespaces", "running")
        for column, key in enumerate(stat_keys):
            if key in self.cards:
                self.stats_grid.addWidget(self.cards[key], 0, column)
        self.alerts_grid = QGridLayout()
        for column, key in enumerate(("pending", "failed", "warnings")):
            if key in self.cards:
                self.alerts_grid.addWidget(self.cards[key], 0, column)
        self.plugin_host = QWidget()
        self.plugin_host.setLayout(self.plugin_grid)

        capacity, capacity_layout = self._panel("Resource allocation", "Current use against node capacity")
        capacity_layout.addWidget(self.usage)
        self.capacity_note = QLabel("Usage metrics are unavailable. Install metrics-server to see live CPU and memory use.")
        self.capacity_note.setWordWrap(True)
        theme.muted(self.capacity_note)
        capacity_layout.addWidget(self.capacity_note)

        self.namespace_table = QTableWidget(0, 3)
        self.namespace_table.setAccessibleName("Pod distribution by namespace")
        self.namespace_table.setHorizontalHeaderLabels(["Namespace", "Pods", "Share"])
        self.namespace_table.verticalHeader().hide()
        self.namespace_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.namespace_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.namespace_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.namespace_table.setFocusPolicy(Qt.StrongFocus)
        self.namespace_table.setMaximumHeight(250)
        self.namespace_table.itemActivated.connect(self._open_namespace_item)
        namespace_panel, namespace_layout = self._panel("Namespaces", "Pod count by namespace")
        namespace_panel_head = namespace_layout.itemAt(0).layout()
        self.all_namespaces_button = QPushButton("All namespaces ›")
        self.all_namespaces_button.setAccessibleName("Open all namespaces")
        self.all_namespaces_button.clicked.connect(lambda: self.action_requested.emit("namespaces"))
        namespace_panel_head.addWidget(self.all_namespaces_button)
        namespace_layout.addWidget(self.namespace_table)
        self.namespace_note = QLabel("Namespace pod counts will appear after cluster data loads.")
        theme.muted(self.namespace_note)
        namespace_layout.addWidget(self.namespace_note)

        self.nodes_table = QTableWidget(0, 4)
        self.nodes_table.setAccessibleName("Node health summary")
        self.nodes_table.setHorizontalHeaderLabels(["Node", "Status", "CPU", "Memory"])
        self.nodes_table.verticalHeader().hide()
        self.nodes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.nodes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nodes_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.nodes_table.setFocusPolicy(Qt.StrongFocus)
        self.nodes_table.setMaximumHeight(250)
        self.nodes_table.itemSelectionChanged.connect(self._node_selection_changed)
        self.nodes_table.itemActivated.connect(self._open_node_item)
        node_panel, node_layout = self._panel("Node health", "Ready state and live resource use")
        node_panel_head = node_layout.itemAt(0).layout()
        self.all_nodes_button = QPushButton("All nodes ›")
        self.all_nodes_button.setAccessibleName("Open all nodes")
        self.all_nodes_button.clicked.connect(lambda: self.action_requested.emit("nodes"))
        node_panel_head.addWidget(self.all_nodes_button)
        node_layout.addWidget(self.nodes_table)
        self.node_note = QLabel("Node health will appear after cluster data loads.")
        theme.muted(self.node_note)
        node_layout.addWidget(self.node_note)
        summaries = QHBoxLayout()
        summaries.addWidget(namespace_panel, 1)
        summaries.addWidget(node_panel, 1)

        recent_panel, recent_layout = self._panel("Recent events")
        recent_header = recent_layout.itemAt(0).layout()
        recent_header.addStretch(1)
        self.back.setStyleSheet("border: 0;")
        recent_header.addWidget(self.back)
        recent_layout.addWidget(self.stack, 1)
        self.title.hide()  # kept as the dynamic heading for the existing drill-down state

        quick_panel, quick_layout = self._panel("Quick actions")
        actions = QHBoxLayout()
        for label, accessible, key in (
            ("Workloads", "Open workload overview", "workloads"),
            ("Node capacity…", "Open node capacity", "capacity"),
            ("Health check…", "Run cluster health check", "health"),
            ("My permissions…", "Review cluster permissions", "permissions"),
        ):
            button = QPushButton(label)
            button.setAccessibleName(accessible)
            button.clicked.connect(lambda _checked=False, action=key: self.action_requested.emit(action))
            actions.addWidget(button)
        quick_layout.addLayout(actions)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 16, 18, 18)
        content_layout.setSpacing(12)
        content_layout.addLayout(page_head)
        content_layout.addWidget(self.health_summary)
        content_layout.addLayout(self.stats_grid)
        content_layout.addLayout(self.alerts_grid)
        content_layout.addWidget(self.plugin_host)
        content_layout.addWidget(capacity)
        content_layout.addLayout(summaries)
        content_layout.addWidget(recent_panel, 1)
        content_layout.addWidget(quick_panel)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

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
        if self.is_cluster_overview and self._data is not None:
            self._set_health_summary(self._data.get("counts", {}))

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
        usage = counts.get("usage")
        self.usage.setVisible(bool(usage))
        self.capacity_note.setVisible(not bool(usage)) if self.is_cluster_overview else None
        if usage:
            for bar, (used, total), unit, digits in ((self.cpu_bar, usage["cpu"], "cores", 1),
                                                     (self.mem_bar, usage["memory"], "GiB", 2)):
                bar.set_usage(used, total, f"{used:.{digits}f} / {total:.{digits}f} {unit} ({bar_percent(used, total)}%)")
        if self.is_cluster_overview:
            self._set_health_summary(counts)
            self._set_namespace_distribution(data.get("namespace_distribution"))
            self._set_nodes(data.get("nodes") or [])
        self.events_table.set_rows(data.get("events") or [])
        if self.focus and not lists.get(self.focus):
            self.focus = None
        self._render()

    def _set_health_summary(self, counts):
        nodes, ready = counts.get("nodes", 0), counts.get("nodes_ready", 0)
        pending, failed, warnings = (counts.get(key, 0) for key in ("pending", "failed", "warnings"))
        if nodes and ready < nodes:
            headline = f"{nodes - ready} of {nodes} nodes need attention"
        elif failed or warnings:
            headline = f"Needs attention · {failed} failed pods · {warnings} warning events"
        else:
            headline = "Cluster healthy"
        detail = f"{ready} of {nodes} nodes ready · {counts.get('running', 0)} running pods · {pending} pending pods"
        if counts.get("by_phase"):
            detail += " · pod counts use Kubernetes phase"
        self.health_summary.setText(f"{headline}\n{detail}")
        self.health_summary.setAccessibleDescription(f"{headline}. {detail}")
        self.health_summary.setStyleSheet(f"background: {theme.BASE}; border: 1px solid {theme.WARN if failed or warnings or (nodes and ready < nodes) else theme.BORDER}; border-radius: 8px; padding: 10px 12px;")

    def _set_namespace_distribution(self, distribution):
        self.namespace_table.clearContents()
        self.namespace_table.setRowCount(0)
        if distribution is None:
            self.namespace_note.setText("Distribution is unavailable in Big Cluster mode to keep refreshes light.")
            self.namespace_note.show()
            return
        rows = sorted(distribution.items(), key=lambda item: (-item[1], item[0].casefold()))
        total = sum(count for _name, count in rows)
        shown, remainder = rows[:7], rows[7:]
        if remainder:
            shown.append(("Other namespaces", sum(count for _name, count in remainder)))
        for row, (name, count) in enumerate(shown):
            self.namespace_table.insertRow(row)
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, name if name != "Other namespaces" else "")
            self.namespace_table.setItem(row, 0, item)
            self.namespace_table.setItem(row, 1, QTableWidgetItem(str(count)))
            share = QProgressBar()
            share.setRange(0, max(total, 1))
            share.setValue(count)
            share.setTextVisible(True)
            share.setFormat(f"{bar_percent(count, total)}%")
            share.setAccessibleName(f"{name}: {count} of {total} pods")
            self.namespace_table.setCellWidget(row, 2, share)
        self.namespace_table.resizeColumnsToContents()
        self.namespace_note.setText("Select a namespace and press Enter to open its pods." if rows else "No pods are currently assigned to a namespace.")
        self.namespace_note.show()
        self.namespace_table.setVisible(bool(rows))

    def _set_nodes(self, nodes):
        self.nodes_table.clearContents()
        self.nodes_table.setRowCount(0)
        ordered = sorted(nodes, key=lambda node: (0 if not node.is_ready else 1, node.name.casefold()))
        shown = ordered[:8]
        self._shown_nodes = {node.name: node for node in shown}
        self.nodes_table.setRowCount(len(shown))
        for row, node in enumerate(shown):
            name_item = QTableWidgetItem(node.name)
            name_item.setData(Qt.UserRole, node)
            status_item = QTableWidgetItem(node.status)
            self.nodes_table.setItem(row, 0, name_item)
            self.nodes_table.setItem(row, 1, status_item)
            self.nodes_table.setItem(row, 2, QTableWidgetItem(self._node_usage_text(node.cpu_used, node.cpu)))
            self.nodes_table.setItem(row, 3, QTableWidgetItem(self._node_usage_text(node.mem_used, node.memory)))
        self.nodes_table.resizeColumnsToContents()
        if len(ordered) > len(shown):
            self.node_note.setText(f"Showing {len(shown)} of {len(ordered)} nodes · open All nodes to browse the full list.")
        else:
            self.node_note.setText("Select a node to inspect it in Details; press Enter to open its node view.")
        self.node_note.show()

    @staticmethod
    def _node_usage_text(used, total):
        if used < 0:
            return "Not reported"
        return f"{bar_percent(used, total)}% · {used:.2f} / {total:.2f}" if total > 0 else f"{used:.2f} used"

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
