from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

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
BAD = "#e5484d"
COLUMNS = 5


class Card(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, tone):
        super().__init__()
        self.key, self.tone = key, tone
        self._clickable = self._selected = False
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
        color = {"warn": theme.WARN, "bad": BAD}.get(self.tone) if v not in (0, "0", "–") else None
        self.value.setStyleSheet(f"font-size: 22px; font-weight: 600;{f' color: {color};' if color else ''}")

    def set_clickable(self, on):
        self._clickable = on
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        self.setToolTip("Click to list them" if on else "")
        self._restyle()

    def set_selected(self, on):
        self._selected = on
        self._restyle()

    def _restyle(self):
        border = theme.ACCENT if self._selected else theme.BORDER
        hover = f"Card:hover {{ border-color: {theme.ACCENT}; }}" if self._clickable else ""
        self.setStyleSheet(f"Card {{ border: 1px solid {border}; border-radius: 6px; }} {hover}")

    def mousePressEvent(self, event):
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


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
        p.setBrush(QColor("#e5484d" if fraction > 0.9 else "#f5a524" if fraction > 0.7 else "#30a46c"))
        p.drawRoundedRect(QRectF(0, 18, self.width() * fraction, 10), 5, 5)


class OverviewView(QWidget):
    """Summary cards over a table that shows recent events, or, after clicking the Pending / Failed /
    Warning cards, the pods or events behind that number (Lens-style Overview pages)."""

    def __init__(self, cards):
        super().__init__()
        self.cards = {}
        self._data = None
        self.focus = None  # drill-down card key currently shown, or None for recent events
        # Shown in place of the (still empty) events table while the first data is on its way, so the page does not
        # look dead. The cards above stay usable; nothing is blocked.
        self.loading_page = LoadingPane()
        self.loading_label, self.loading_clock = self.loading_page.label, self.loading_page.clock
        self._load_clock = QElapsedTimer()
        self._load_timer = QTimer(self, interval=1000)
        self._load_timer.timeout.connect(self._loading_tick)
        grid = QGridLayout()
        for i, (key, title, tone) in enumerate(cards):
            self.cards[key] = Card(key, title, tone)
            self.cards[key].clicked.connect(self._on_card)
            grid.addWidget(self.cards[key], i // COLUMNS, i % COLUMNS)
        self.plugin_cards = {}  # card id -> Card, added by plugins (see set_plugin_cards)
        self.plugin_grid = QGridLayout()
        self.title = QLabel("Recent events")
        self.title.setStyleSheet("font-weight: 600; padding-top: 8px;")
        self.back = QPushButton("Show recent events")
        self.back.clicked.connect(lambda: self._set_focus(None))
        self.back.hide()
        head = QHBoxLayout()
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.back)
        self.cpu_bar, self.mem_bar = UsageBar("CPU in use"), UsageBar("Memory in use")
        usage = QHBoxLayout()
        usage.addWidget(self.cpu_bar)
        usage.addSpacing(16)
        usage.addWidget(self.mem_bar)
        self.usage = QWidget()
        self.usage.setLayout(usage)
        self.usage.hide()  # only shown when metrics-server reports usage
        self.events_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False, customizable=False)
        self.pods_table = ResourceTable(POD_COLUMNS_BASIC, "Pod", customizable=False)
        self.warnings_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False, customizable=False)
        self.stack = QStackedWidget()
        for t in self.tables():
            self.stack.addWidget(t)
        self.stack.addWidget(self.loading_page)
        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addLayout(self.plugin_grid)
        lay.addWidget(self.usage)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)

    def tables(self):
        return [self.events_table, self.pods_table, self.warnings_table]

    def set_loading(self):
        """A load started. Only the first one (nothing to show yet) gets an indicator; routine refreshes stay quiet."""
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
        if self._data is None:  # with data on screen the status bar message is enough
            self.loading_page.show_failed(f"Load failed: {message}")
            self.stack.setCurrentWidget(self.loading_page)

    def set_plugin_cards(self, cards):
        """Cards plugins add under the built-in ones: [(card id, title)]. Cards that stay keep their value."""
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
        lists = data.get("lists", {})
        for key, card in self.cards.items():
            card.set_value(data["counts"].get(key, "–"))
            card.set_clickable(key in DRILL_DOWN and bool(lists.get(key)))
        usage = data["counts"].get("usage")
        self.usage.setVisible(bool(usage))
        if usage:
            for bar, (used, total), unit, digits in ((self.cpu_bar, usage["cpu"], "cores", 1),
                                                     (self.mem_bar, usage["memory"], "GiB", 2)):
                bar.set_usage(used, total, f"{used:.{digits}f} / {total:.{digits}f} {unit} ({bar_percent(used, total)}%)")
        self.events_table.set_rows(data["events"])
        if self.focus and not lists.get(self.focus):
            self.focus = None  # the problem went away: fall back to recent events
        self._render()

    def _on_card(self, key):
        self._set_focus(None if self.focus == key else key)  # clicking the selected card again closes it

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
