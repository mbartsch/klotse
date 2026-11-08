"""Usage history of one pod, node or workload: CPU and memory charts. Looking only.

Where the data comes from is the `sampler` the caller gives (see `k8s/metrics_history`): with only metrics-server there is no history, so the
window reads it every `interval_s` seconds and keeps the points in memory from the moment it opened, which the window says. The charts show
the object's request and limit (a pod) or allocatable amount (a node) as dashed lines."""
import time

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..k8s import metrics_history as mh
from ..workers import Worker
from . import theme
from .line_chart import LineChart

NOTE_SAMPLED = ("metrics-server keeps no history, so this chart starts when the window opens and is kept in memory only. "
                "Prometheus or VictoriaMetrics would show earlier data.")


class MetricsDialog(QDialog):
    def __init__(self, title, sample, refs=None, available=True, interval_s=15, parent=None):
        """`sample()` returns {line: (timestamp, cores, MiB)} (called on a worker); `refs` is mh.reference_lines(...);
        `available` False when the cluster has no metrics API (then nothing is sampled)."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 560)
        self.sample_fn, self.refs, self.available, self.interval_s = sample, refs or {"cpu": {}, "mem": {}}, available, interval_s
        self.history, self.started = mh.History(), time.time()
        self._busy, self._closed, self._errors = False, False, 0
        self.cpu_chart = LineChart("CPU", mh.format_cores)
        self.mem_chart = LineChart("Memory", mh.format_mib)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel(NOTE_SAMPLED)
        note.setWordWrap(True)
        theme.muted(note)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.cpu_chart, 1)
        lay.addWidget(self.mem_chart, 1)
        lay.addWidget(note)
        lay.addLayout(buttons)
        self.timer = QTimer(self, interval=int(interval_s * 1000))
        self.timer.timeout.connect(self.sample)
        self._redraw()
        if available:
            self.timer.start()
            self.sample()  # the first point now, not after the first interval
        else:
            self.status.setText("This cluster does not serve the metrics API (metrics.k8s.io), so there is nothing to chart. "
                                "Install metrics-server, or a Prometheus or VictoriaMetrics source.")

    # ---- sampling -------------------------------------------------------------------------------------------------
    def sample(self):
        """Read metrics-server once on a worker (skipped while the previous read is still running)."""
        if self._busy or self._closed or not self.available:
            return
        self._busy = True
        worker = Worker(self.sample_fn)
        worker.setAutoDelete(False)  # keep its signals alive until the queued answer arrives
        self._worker = worker
        worker.signals.result.connect(self._got)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    def _got(self, sample):
        self._busy = False
        if self._closed:
            return
        self._errors = 0
        self.history.add(sample or {}, time.time())
        self._redraw()

    def _failed(self, message):
        self._busy = False
        if self._closed:
            return
        self._errors += 1
        self.status.setText(f"Could not read metrics-server: {message.splitlines()[0] if message else 'failed'}")

    def _pause(self, paused):
        if paused:
            self.timer.stop()
        else:
            self.timer.start()
            self.sample()
        self._redraw()

    def clear(self):
        self.history.clear()
        self.started = time.time()
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
