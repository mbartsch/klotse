"""Workload > Metrics query…: quick PromQL against the Prometheus / VictoriaMetrics source of the cluster, so the numbers can be read without a graph
tool. The dialog shows a query box (pre-filled from the workload's presets), the latest value of every series, and says what failed. It runs
nothing itself: `run(promql, on_result, on_error)` is the workspace's worker. metrics-server cannot run queries; Usage history covers it."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..k8s import prometheus
from . import theme
from .line_chart import LineChart

GRAPH_RANGES = (("15 minutes", 900), ("1 hour", 3600), ("6 hours", 21600), ("24 hours", 86400))


class MetricsQueryDialog(QWidget):
    source_requested = Signal()  # the Source… button: choose the Prometheus / VictoriaMetrics source for this window's queries

    def __init__(self, title, source_text, presets, run, parent=None, run_range=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAccessibleName("Metrics query")
        self._run = run
        self._run_range = run_range  # run_range(promql, seconds, on_result, on_error): the graph's range query
        self._presets = list(presets)
        self.source = QLabel(f"Source: {source_text}")
        theme.muted(self.source)
        self.source_button = QPushButton("Source…")
        self.source_button.setToolTip("Choose the Prometheus or VictoriaMetrics source for this cluster (the same as Usage history)")
        self.source_button.clicked.connect(self.source_requested.emit)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source, 1)
        source_row.addWidget(self.source_button)
        self.preset_box = QComboBox()
        self.preset_box.setAccessibleName("Quick query")
        for label, query in self._presets:
            self.preset_box.addItem(label, query)
        self.preset_box.addItem("Own query", "")
        self.preset_box.currentIndexChanged.connect(self._preset_changed)
        self.query = QLineEdit()
        self.query.setAccessibleName("PromQL query")
        self.query.setPlaceholderText("PromQL, for example sum(rate(container_cpu_usage_seconds_total[5m]))")
        self.query.returnPressed.connect(self.run_query)
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_query)
        self.graph_range = QComboBox()
        self.graph_range.setAccessibleName("Graph time range")
        for label, seconds in GRAPH_RANGES:
            self.graph_range.addItem(label, seconds)
        self.graph_button = QPushButton("Graph")
        self.graph_button.setToolTip("Draw the query over the chosen time range, one line per series")
        self.graph_button.setEnabled(run_range is not None)
        self.graph_button.clicked.connect(self.graph_query)
        self.chart = LineChart("Graph", lambda value: f"{value:.6g}")
        self.chart.setAccessibleName("Query graph")
        self.chart.setMinimumHeight(180)
        top = QHBoxLayout()
        top.addWidget(self.preset_box)
        top.addWidget(self.query, 1)
        top.addWidget(self.run_button)
        top.addWidget(self.graph_range)
        top.addWidget(self.graph_button)
        self.table = QTableWidget(0, 2)
        self.table.setAccessibleName("Query results")
        self.table.setHorizontalHeaderLabels(["Series", "Latest value"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Query status")
        self.note = QLabel("Queries are read only. metrics-server cannot run queries: use Usage history for its CPU and memory.")
        self.note.setWordWrap(True)
        theme.muted(self.note)
        layout = QVBoxLayout(self)
        layout.addLayout(source_row)
        for widget in (top, self.chart, self.table, self.status, self.note):
            if isinstance(widget, QHBoxLayout):
                layout.addLayout(widget)
            else:
                layout.addWidget(widget, 1 if widget is self.table else 0)
        if self._presets:
            self._preset_changed()
        self.resize(760, 420)

    def set_source(self, text, reason=""):
        """Show the source the queries use (or that none is set, and why)."""
        self.source.setText(f"Source: {text}")
        self.status.setText(f"No source: {reason}. Choose one with Source…." if reason else "")

    def _preset_changed(self, *_):
        query = self.preset_box.currentData()
        if query:
            self.query.setText(query)
        self.query.setFocus()

    def run_query(self):
        promql = self.query.text().strip()
        if not promql:
            self.status.setText("Write a query first.")
            return
        self.run_button.setEnabled(False)
        self.status.setText("Running…")
        self._run(promql, self._show, self._fail)

    def graph_query(self):
        promql = self.query.text().strip()
        if not promql or self._run_range is None:
            self.status.setText("Write a query first.")
            return
        self.graph_button.setEnabled(False)
        self.status.setText("Drawing…")
        self._run_range(promql, self.graph_range.currentData(), self._draw, self._graph_failed)

    def _draw(self, lines):
        try:
            self.graph_button.setEnabled(True)
            self.chart.set_data(lines, placeholder="No samples in this time range for this query.")
            self.status.setText(f"{len(lines)} lines over {self.graph_range.currentText()}.")
        except RuntimeError:
            pass

    def _graph_failed(self, message):
        try:
            self.graph_button.setEnabled(True)
            self.status.setText(f"The graph query failed: {message}")
        except RuntimeError:
            pass

    def _show(self, series):
        try:
            self.run_button.setEnabled(True)
            self.table.setRowCount(len(series))
            for row, (labels, value) in enumerate(sorted(series, key=lambda s: prometheus.series_text(s[0]))):
                self.table.setItem(row, 0, QTableWidgetItem(prometheus.series_text(labels)))
                item = QTableWidgetItem(f"{value:.6g}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, 1, item)
            self.status.setText(f"{len(series)} series." if series else "No series matched this query.")
        except RuntimeError:  # the window was closed while the query ran
            pass

    def _fail(self, message):
        try:
            self.run_button.setEnabled(True)
            self.table.setRowCount(0)
            self.status.setText(f"The query failed: {message}")
        except RuntimeError:
            pass
