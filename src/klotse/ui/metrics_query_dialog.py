"""Workload > Metrics query…: quick PromQL against the Prometheus / VictoriaMetrics source of the cluster, so the numbers can be read without a graph
tool. The dialog shows a query box (pre-filled from the workload's presets), the Run and Graph controls above a table of every series (all its
labels, the latest value and the time of that sample), and a graph of the query over a chosen time range underneath. It runs nothing itself:
`run(promql, on_result, on_error)` and `run_range(promql, seconds, on_result, on_error)` are the workspace's workers. metrics-server cannot run
queries; Usage history covers it."""
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..k8s import prometheus
from . import theme
from .line_chart import LineChart

GRAPH_RANGES = (("15 minutes", 900), ("1 hour", 3600), ("6 hours", 21600), ("24 hours", 86400))
REPEAT_EVERY = (("Repeat: off", 0), ("every 5 s", 5), ("every 15 s", 15), ("every 30 s", 30), ("every 1 min", 60), ("every 5 min", 300))


def _when(stamp):
    """The sample time, local, to the second (the table shows how old each value is)."""
    try:
        return datetime.fromtimestamp(float(stamp)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


class MetricsQueryDialog(QWidget):
    source_requested = Signal()  # the Source… button: choose the Prometheus / VictoriaMetrics source for this window's queries

    def __init__(self, title, source_text, presets, run, parent=None, run_range=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAccessibleName("Metrics query")
        self._run = run
        self._closed = False  # set when the window closes: no new query starts and no result is shown after that
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
        top = QHBoxLayout()
        top.addWidget(self.preset_box)
        top.addWidget(self.query, 1)
        # Run and Graph sit above the table they fill: the table takes the instant answer, the graph the range
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_query)
        self.graph_range = QComboBox()
        self.graph_range.setAccessibleName("Graph time range")
        for label, seconds in GRAPH_RANGES:
            self.graph_range.addItem(label, seconds)
        self.graph_button = QPushButton("Graph")
        self.graph_button.setToolTip("Draw the query over the chosen time range, one line per series, under the table")
        self.graph_button.setEnabled(run_range is not None)
        self.graph_button.clicked.connect(self.graph_query)
        actions = QHBoxLayout()
        actions.addWidget(self.run_button)
        self.repeat_box = QComboBox()
        self.repeat_box.setAccessibleName("Repeat the query")
        self.repeat_box.setToolTip("Run the query again every few seconds or minutes, adding its rows below (until Clear)")
        for label, seconds in REPEAT_EVERY:
            self.repeat_box.addItem(label, seconds)
        self.repeat_box.currentIndexChanged.connect(self._repeat_changed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._repeat_due)
        actions.addWidget(self.repeat_box)
        actions.addWidget(self.graph_range)
        actions.addWidget(self.graph_button)
        actions.addStretch(1)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Remove the results of earlier runs from the table")
        self.clear_button.clicked.connect(self.clear_results)
        actions.addWidget(self.clear_button)
        self.table = QTableWidget(0, 3)
        self.table.setAccessibleName("Query results")
        self.table.setHorizontalHeaderLabels(["At", "Series", "Latest value"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.chart = LineChart("Graph", lambda value: f"{value:.6g}")
        self.chart.setAccessibleName("Query graph")
        self.chart.setMinimumHeight(180)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Query status")
        self.note = QLabel("Queries are read only. metrics-server cannot run queries: use Usage history for its CPU and memory.")
        self.note.setWordWrap(True)
        theme.muted(self.note)
        layout = QVBoxLayout(self)
        layout.addLayout(source_row)
        layout.addLayout(top)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.chart, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.note)
        if self._presets:
            self._preset_changed()
        self.resize(760, 560)

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
        if self._closed:
            return
        promql = self.query.text().strip()
        if not promql:
            self.status.setText("Write a query first.")
            return
        self.run_button.setEnabled(False)
        self.status.setText("Running…")
        self._run(promql, self._show, self._fail)

    def graph_query(self):
        if self._closed:
            return
        promql = self.query.text().strip()
        if not promql or self._run_range is None:
            self.status.setText("Write a query first.")
            return
        self.graph_button.setEnabled(False)
        self.status.setText("Drawing…")
        self._run_range(promql, self.graph_range.currentData(), self._draw, self._graph_failed)

    def _draw(self, lines):
        if self._closed:
            return
        try:
            self.graph_button.setEnabled(True)
            self.chart.set_data(lines, placeholder="No samples in this time range for this query.")
            self.status.setText(f"{len(lines)} lines over {self.graph_range.currentText()}.")
        except RuntimeError:
            pass

    def _graph_failed(self, message):
        if self._closed:
            return
        try:
            self.graph_button.setEnabled(True)
            self.status.setText(f"The graph query failed: {message}")
        except RuntimeError:
            pass

    def _repeat_changed(self, *_):
        seconds = self.repeat_box.currentData() or 0
        if seconds:
            self._timer.start(seconds * 1000)
            self.status.setText(f"Repeating {self.repeat_box.currentText()}.")
            self.run_query()
        else:
            self._timer.stop()
            self.status.setText("Repeat is off.")

    def _repeat_due(self):
        """A repeat is skipped while the previous run is still waiting for the source, so runs never pile up."""
        if self.run_button.isEnabled() and self.query.text().strip():
            self.run_query()

    def stop_repeat(self):
        """Called when the window closes: no repeats after it is gone."""
        self._timer.stop()

    def closeEvent(self, event):
        self._closed = True
        self.stop_repeat()
        super().closeEvent(event)

    def clear_results(self):
        """Clear: remove the rows of every earlier run."""
        self.table.setRowCount(0)
        self.status.setText("Cleared.")

    def _show(self, series):
        """`series`: [(labels, value, time)] in the order the source answered; nothing is cleaned up or sorted. Each run adds its rows below
        the rows of earlier runs, the time (first column) says when each value was sampled, until Clear. A window closed meanwhile drops it."""
        if self._closed:
            return
        try:
            self.run_button.setEnabled(True)
            start = self.table.rowCount()
            self.table.setRowCount(start + len(series))
            for offset, (labels, value, stamp) in enumerate(series):
                row = start + offset
                self.table.setItem(row, 0, QTableWidgetItem(_when(stamp)))
                self.table.setItem(row, 1, QTableWidgetItem(prometheus.series_text(labels)))
                item = QTableWidgetItem(f"{value:.6g}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, 2, item)
            self.table.scrollToBottom()
            self.status.setText(f"{len(series)} series added; {self.table.rowCount()} rows." if series else "No series matched this query.")
        except RuntimeError:  # the window was closed while the query ran
            pass

    def _fail(self, message):
        if self._closed:
            return
        try:
            self.run_button.setEnabled(True)  # the rows of earlier runs stay until Clear
            self.status.setText(f"The query failed: {message}")
        except RuntimeError:
            pass
