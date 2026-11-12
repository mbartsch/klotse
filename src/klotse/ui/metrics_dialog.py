"""Usage history of one pod, node or workload: CPU and memory charts. Looking only.

Two sources, chosen in the window:
  * **metrics-server** (always there when it is installed) keeps no history, so the window reads it every `interval_s` seconds and keeps the
    points in memory from the moment it opened (`k8s/metrics_history`), which the window says;
  * **Prometheus / VictoriaMetrics** (when the cluster has one, found automatically or given by the user) holds real history: the window asks
    it for the last 15 minutes to 7 days and refreshes every 30 s (`k8s/prometheus`).
The caller gives the metrics-server `sample` and may add the Prometheus source later with `set_prometheus` once it has found and checked
one; the window then switches to it. The charts show the object's request and limit (a pod) or allocatable amount (a node) as dashed lines."""
import time

from PySide6.QtCore import QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QToolButton, QVBoxLayout)

from ..k8s import metrics_history as mh
from ..k8s import prometheus
from ..workers import Worker
from . import theme
from .line_chart import LineChart
from .dialog_sizing import resize_dialog
from .dialog_actions import action_box

NOTE_SAMPLED = ("metrics-server keeps no history, so this chart starts when the window opens and is kept in memory only. "
                "Prometheus or VictoriaMetrics would show earlier data: use Source… to point Klotse at one.")
NOTE_PROMETHEUS = ("History from the Prometheus or VictoriaMetrics service, read through the Kubernetes API server (no port-forward). "
                   "It needs the kubelet cAdvisor metrics; an empty chart usually means they are not scraped or labelled differently.")
SAMPLED, REMOTE = "sampled", "prometheus"
REFRESH_S = 30


class MetricsDialog(QDialog):
    source_requested = Signal()  # the Source… button: the owner asks for a Prometheus address
    go_to_requested = Signal(str)  # series name under the pointer

    def __init__(self, title, sample, refs=None, available=True, interval_s=15, parent=None, allow_source=True, object_options=None):
        """`sample()` returns {line: (timestamp, cores, MiB)} (called on a worker); `refs` is mh.reference_lines(...);
        `available` False when the cluster has no metrics API (then nothing is sampled). `allow_source` False (several objects picked
        together) hides Source…: Prometheus is asked about one object at a time."""
        super().__init__(parent)
        self.setWindowTitle(title)
        resize_dialog(self, 920, 600)
        self.sample_fn, self.refs, self.available, self.interval_s = sample, refs or {"cpu": {}, "mem": {}}, available, interval_s
        self.history, self.started = mh.History(), time.time()
        self._busy, self._closed, self._errors = False, False, 0
        self.fetch_remote, self.remote_label, self.remote_note = None, "", ""
        self._status_text = ""
        self.object_options = dict(object_options or {})
        self.cpu_chart = LineChart("CPU", mh.format_cores)
        self.mem_chart = LineChart("Memory", mh.format_mib)
        self._hovered_line = ""
        self.status = QLabel("")
        self.status.setAccessibleName("Usage history status")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.source_box = QComboBox()
        self.source_box.setAccessibleName("Usage history data source")
        self.source_box.addItem("metrics-server (sampled while open)", SAMPLED)
        self.range_box = QComboBox()
        self.range_box.setAccessibleName("Usage history time range")
        for label, seconds in prometheus.RANGES:
            self.range_box.addItem(f"Last {label}", seconds)
        self.range_box.setCurrentIndex(1)
        self.range_box.setEnabled(False)
        self.source_button = QPushButton("Source…")
        self.source_button.setAccessibleName("Configure usage history source")
        self.source_button.setToolTip("Point Klotse at a Prometheus or VictoriaMetrics service, or let it look for one")
        self.source_button.clicked.connect(self.source_requested.emit)
        self.source_button.setVisible(allow_source)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setAccessibleName("Pause or resume usage history sampling")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setAccessibleName("Clear usage history samples")
        self.clear_button.clicked.connect(self.clear)
        self.go_to_button = QToolButton()
        self.go_to_button.setText("Go to object")
        self.go_to_button.setAccessibleName("Go to an object shown in usage history")
        self.go_to_button.setPopupMode(QToolButton.InstantPopup)
        self.go_to_menu = QMenu(self.go_to_button)
        self.go_to_button.setMenu(self.go_to_menu)
        self.go_to_button.setToolTip("Move to the object represented by the chart line under the pointer")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.note = QLabel(NOTE_SAMPLED)
        self.note.setWordWrap(True)
        theme.muted(self.note)
        top = QHBoxLayout()
        top.addWidget(QLabel("Source"))
        top.addWidget(self.source_box)
        top.addWidget(self.range_box)
        top.addWidget(self.source_button)
        top.addStretch(1)
        buttons = action_box(self, (self.pause_button, QDialogButtonBox.ActionRole),
                             (self.clear_button, QDialogButtonBox.ActionRole),
                             (self.go_to_button, QDialogButtonBox.ActionRole),
                             (close, QDialogButtonBox.RejectRole))
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.cpu_chart, 1)
        lay.addWidget(self.mem_chart, 1)
        lay.addWidget(self.note)
        lay.addWidget(buttons)
        self.timer = QTimer(self, interval=int(interval_s * 1000))
        self.timer.timeout.connect(self.refresh)
        self.source_box.currentIndexChanged.connect(lambda _=None: self._source_changed())
        self.cpu_chart.hovered_line.connect(self._set_hovered_line)
        self.mem_chart.hovered_line.connect(self._set_hovered_line)
        self.cpu_chart.line_activated.connect(self._activate_line)
        self.mem_chart.line_activated.connect(self._activate_line)
        self.range_box.currentIndexChanged.connect(lambda _=None: self.query())
        self._redraw()
        if available:
            self.timer.start()
            self.sample()  # the first point now, not after the first interval
        else:
            self._set_status("This cluster does not serve the metrics API (metrics.k8s.io), so there is nothing to chart from metrics-server. "
                                "Install it, or use Source… to point at a Prometheus or VictoriaMetrics service.")

    def _set_hovered_line(self, name):
        if name:
            self._hovered_line = name

    def _activate_line(self, name):
        self._set_hovered_line(name)
        if name != "total":
            self.go_to_requested.emit(name)

    def _refresh_object_menu(self):
        self.go_to_menu.clear()
        names = dict(self.object_options)
        for chart in (self.cpu_chart, self.mem_chart):
            for name in chart.series:
                if name == "total":
                    continue
                names.setdefault(name, name)
        for name, label in names.items():
            if name == "total":
                continue
            action = self.go_to_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, value=name: self.go_to_requested.emit(value))

    # ---- which source ---------------------------------------------------------------------------------------------
    def mode(self):
        return self.source_box.currentData()

    def set_prometheus(self, label, fetch, select=True):
        """Offer the Prometheus / VictoriaMetrics source: `fetch(seconds)` returns ({line: [(t, cores)]}, {line: [(t, MiB)]}) and runs on a
        worker; `label` names the service. Replaces an earlier offer. Switches to it when `select`."""
        self.clear_prometheus()
        self.fetch_remote, self.remote_label = fetch, label
        self.source_box.addItem(f"Prometheus / VictoriaMetrics · {label}", REMOTE)
        if select:
            self.source_box.setCurrentIndex(self.source_box.count() - 1)

    def clear_prometheus(self):
        """Stop offering the Prometheus source (back to metrics-server)."""
        self.set_source_note("")
        index = self.source_box.findData(REMOTE)
        if index >= 0:
            self.source_box.setCurrentIndex(0)
            self.source_box.removeItem(index)
        self.fetch_remote, self.remote_label = None, ""

    def set_source_note(self, message):
        """Keep a source-validation problem visible while sampled readings continue to update the chart."""
        self.remote_note = message
        self._set_status(self._status_text)

    def _set_status(self, text):
        self._status_text = text
        if self.remote_note and self.mode() == SAMPLED:
            text = f"{text}\n{self.remote_note}" if text else self.remote_note
        self.status.setText(text)

    def _source_changed(self):
        self.set_source_note("")
        remote = self.mode() == REMOTE
        self.range_box.setEnabled(remote)
        self.pause_button.setEnabled(True)
        self.note.setText(NOTE_PROMETHEUS if remote else NOTE_SAMPLED)
        self._errors = 0
        if remote:
            self.timer.setInterval(REFRESH_S * 1000)
            if not self.pause_button.isChecked():
                self.timer.start()
            self.query()
        else:
            self.timer.setInterval(int(self.interval_s * 1000))
            if self.available and not self.pause_button.isChecked():
                self.timer.start()
                self.sample()
            self._redraw()

    def refresh(self):
        """The timer: read the current source again."""
        self.query() if self.mode() == REMOTE else self.sample()

    # ---- workers --------------------------------------------------------------------------------------------------
    def _start(self, fn, got, failed):
        self._busy = True
        self._asked = self.mode()  # the source this read is for; the user or a found Prometheus may switch before it answers
        worker = Worker(fn)
        worker.setAutoDelete(False)  # keep its signals alive until the queued answer arrives
        self._worker = worker
        worker.signals.result.connect(got)
        worker.signals.error.connect(failed)
        QThreadPool.globalInstance().start(worker)

    def sample(self):
        """Read metrics-server once on a worker (skipped while a read is running)."""
        if self._busy or self._closed or not self.available or self.mode() != SAMPLED:
            return
        self._start(self.sample_fn, self._got, lambda message: self._failed("metrics-server", message))

    def query(self):
        """Ask Prometheus / VictoriaMetrics for the chosen range on a worker."""
        if self._busy or self._closed or self.mode() != REMOTE or self.fetch_remote is None:
            return
        seconds, fetch = self.range_box.currentData(), self.fetch_remote
        self._set_status(f"Asking {self.remote_label}…")
        self._start(lambda: fetch(seconds), self._got_remote, lambda message: self._failed(self.remote_label, message))

    def _stale(self):
        """The answer that just came is for a source that is no longer chosen. The switch could not start its own read, because
        this one was still running, so start it now instead of leaving the chart empty until the timer fires."""
        if self._closed:
            return True
        if getattr(self, "_asked", self.mode()) == self.mode():
            return False
        self.refresh()
        return True

    def _got(self, sample):
        self._busy = False
        if self._stale():
            return
        self._errors = 0
        self.history.add(sample or {}, time.time())
        self._redraw()

    def _got_remote(self, answer):
        self._busy = False
        if self._stale():
            return
        self._errors = 0
        cpu, mem = answer
        empty = "Prometheus answered with no data for this object. The kubelet cAdvisor metrics may not be scraped, or are labelled differently."
        self.cpu_chart.set_data(cpu, self.refs.get("cpu"), placeholder=empty)
        self.mem_chart.set_data(mem, self.refs.get("mem"), placeholder=empty)
        self._refresh_object_menu()
        points = max((len(p) for p in list(cpu.values()) + list(mem.values())), default=0)
        self._set_status(f"Prometheus / VictoriaMetrics · {self.remote_label} · {self.range_box.currentText().lower()} · {points} points per line"
                            f" · every {REFRESH_S} s" + (" · paused" if self.pause_button.isChecked() else ""))

    def _failed(self, source, message):
        self._busy = False
        if self._stale():
            return
        self._errors += 1
        self._set_status(f"Could not read {source}: {message.splitlines()[0] if message else 'failed'}")

    def _pause(self, paused):
        if paused:
            self.timer.stop()
        else:
            self.timer.start()
            self.refresh()
        if self.mode() == SAMPLED:
            self._redraw()

    def clear(self):
        """Forget the sampled history and start again (Prometheus keeps its own)."""
        self.history.clear()
        self.started = time.time()
        if self.mode() == SAMPLED:
            self._redraw()

    def _redraw(self):
        waiting = "Waiting for the first metrics-server reading…"
        self.cpu_chart.set_data(self.history.series("cpu"), self.refs.get("cpu"), placeholder=waiting)
        self.mem_chart.set_data(self.history.series("mem"), self.refs.get("mem"), placeholder=waiting)
        self._refresh_object_menu()
        if self.available and not self._errors:
            count = self.history.count()
            first = self.history.first_time()
            since = time.strftime("%H:%M:%S", time.localtime(first if first else self.started))
            self._set_status(f"metrics-server · {count} sample{'s' if count != 1 else ''} since {since} · every {self.interval_s:g} s"
                                + (" · paused" if self.pause_button.isChecked() else ""))

    def done(self, result):
        self._closed = True
        self.timer.stop()
        super().done(result)
