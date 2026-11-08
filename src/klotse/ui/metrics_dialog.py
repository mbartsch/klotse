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
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..k8s import metrics_history as mh
from ..k8s import prometheus
from ..workers import Worker
from . import theme
from .line_chart import LineChart

NOTE_SAMPLED = ("metrics-server keeps no history, so this chart starts when the window opens and is kept in memory only. "
                "Prometheus or VictoriaMetrics would show earlier data: use Source… to point Klotse at one.")
NOTE_PROMETHEUS = ("History from the Prometheus or VictoriaMetrics service, read through the Kubernetes API server (no port-forward). "
                   "It needs the kubelet cAdvisor metrics; an empty chart usually means they are not scraped or labelled differently.")
SAMPLED, REMOTE = "sampled", "prometheus"
REFRESH_S = 30


class MetricsDialog(QDialog):
    source_requested = Signal()  # the Source… button: the owner asks for a Prometheus address

    def __init__(self, title, sample, refs=None, available=True, interval_s=15, parent=None):
        """`sample()` returns {line: (timestamp, cores, MiB)} (called on a worker); `refs` is mh.reference_lines(...);
        `available` False when the cluster has no metrics API (then nothing is sampled)."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(920, 600)
        self.sample_fn, self.refs, self.available, self.interval_s = sample, refs or {"cpu": {}, "mem": {}}, available, interval_s
        self.history, self.started = mh.History(), time.time()
        self._busy, self._closed, self._errors = False, False, 0
        self.fetch_remote, self.remote_label, self.remote_note = None, "", ""
        self.cpu_chart = LineChart("CPU", mh.format_cores)
        self.mem_chart = LineChart("Memory", mh.format_mib)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.source_box = QComboBox()
        self.source_box.addItem("metrics-server (sampled while open)", SAMPLED)
        self.range_box = QComboBox()
        for label, seconds in prometheus.RANGES:
            self.range_box.addItem(f"Last {label}", seconds)
        self.range_box.setCurrentIndex(1)
        self.range_box.setEnabled(False)
        self.source_button = QPushButton("Source…")
        self.source_button.setToolTip("Point Klotse at a Prometheus or VictoriaMetrics service, or let it look for one")
        self.source_button.clicked.connect(self.source_requested.emit)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
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
        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.cpu_chart, 1)
        lay.addWidget(self.mem_chart, 1)
        lay.addWidget(self.note)
        lay.addLayout(buttons)
        self.timer = QTimer(self, interval=int(interval_s * 1000))
        self.timer.timeout.connect(self.refresh)
        self.source_box.currentIndexChanged.connect(lambda _=None: self._source_changed())
        self.range_box.currentIndexChanged.connect(lambda _=None: self.query())
        self._redraw()
        if available:
            self.timer.start()
            self.sample()  # the first point now, not after the first interval
        else:
            self.status.setText("This cluster does not serve the metrics API (metrics.k8s.io), so there is nothing to chart from metrics-server. "
                                "Install it, or use Source… to point at a Prometheus or VictoriaMetrics service.")

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
        index = self.source_box.findData(REMOTE)
        if index >= 0:
            self.source_box.setCurrentIndex(0)
            self.source_box.removeItem(index)
        self.fetch_remote, self.remote_label = None, ""

    def _source_changed(self):
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
        self.status.setText(f"Asking {self.remote_label}…")
        self._start(lambda: fetch(seconds), self._got_remote, lambda message: self._failed(self.remote_label, message))

    def _got(self, sample):
        self._busy = False
        if self._closed or self.mode() != SAMPLED:
            return
        self._errors = 0
        self.history.add(sample or {}, time.time())
        self._redraw()

    def _got_remote(self, answer):
        self._busy = False
        if self._closed or self.mode() != REMOTE:
            return
        self._errors = 0
        cpu, mem = answer
        empty = "Prometheus answered with no data for this object. The kubelet cAdvisor metrics may not be scraped, or are labelled differently."
        self.cpu_chart.set_data(cpu, self.refs.get("cpu"), placeholder=empty)
        self.mem_chart.set_data(mem, self.refs.get("mem"), placeholder=empty)
        points = max((len(p) for p in list(cpu.values()) + list(mem.values())), default=0)
        self.status.setText(f"Prometheus / VictoriaMetrics · {self.remote_label} · {self.range_box.currentText().lower()} · {points} points per line"
                            f" · every {REFRESH_S} s" + (" · paused" if self.pause_button.isChecked() else ""))

    def _failed(self, source, message):
        self._busy = False
        if self._closed:
            return
        self._errors += 1
        self.status.setText(f"Could not read {source}: {message.splitlines()[0] if message else 'failed'}")

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
        if self.available and not self._errors:
            count = self.history.count()
            first = self.history.first_time()
            since = time.strftime("%H:%M:%S", time.localtime(first if first else self.started))
            self.status.setText(f"metrics-server · {count} sample{'s' if count != 1 else ''} since {since} · every {self.interval_s:g} s"
                                + (" · paused" if self.pause_button.isChecked() else ""))

    def done(self, result):
        self._closed = True
        self.timer.stop()
        super().done(result)
