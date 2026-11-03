import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHeaderView, QLabel, QPlainTextEdit, QProgressBar, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

POLL_MS = 1500
TIMEOUT_S = 300
COLORS = {"complete": "#2fbf71", "failed": "#e5484d", "timeout": "#f5a524", "progress": None}


def evaluate(target, st, elapsed, timeout):
    """(state, text) for a scale operation; state is progress | complete | failed | timeout."""
    live = [p for p in st["pods"] if not p["deleting"]]
    failing = [p for p in live if p["issue"]]
    ready = st["ready"]
    # complete only once surplus pods are really gone, not merely marked Terminating
    if st["observed"] and ready == target and len(st["pods"]) == target:
        return "complete", f"Complete: {ready}/{target} ready in {elapsed:.0f}s"
    if st["deadline_exceeded"]:
        return "failed", f"Failed: rollout exceeded its progress deadline ({ready}/{target} ready)"
    terminating = len(st["pods"]) - len(live)
    problem = (f"; {len(failing)} failing" if failing else "") + (f"; {terminating} still terminating" if terminating else "")
    if elapsed >= timeout:
        return "timeout", f"Timed out after {elapsed:.0f}s: {ready}/{target} ready{problem}"
    if not st["observed"]:
        return "progress", "Waiting for the controller to pick up the change…"
    parts = [f"{ready}/{target} ready"]
    starting = len(live) - ready - len(failing)
    if starting > 0:
        parts.append(f"{starting} starting")
    if failing:
        parts.append(f"⚠ {len(failing)} failing")
    if len(live) > target:
        parts.append(f"{len(live) - target} to remove")
    if len(st["pods"]) > len(live):
        parts.append(f"{len(st['pods']) - len(live)} terminating")
    return "progress", f"In progress ({elapsed:.0f}s): " + ", ".join(parts)


class ScaleMonitor(QWidget):
    """Polls a workload after a scale and shows progress, failing pods, warning events and the final result."""

    finished = Signal(str, str)  # state, text
    polled = Signal()

    def __init__(self, run, service, kind, name, namespace, start, target, poll_ms=None, timeout_s=None, parent=None):
        super().__init__(parent)
        self._run, self.service = run, service
        self.kind, self.name, self.namespace = kind, name, namespace
        self.poll_ms = POLL_MS if poll_ms is None else poll_ms
        self.timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
        self._gen = 0
        self._stopped = False
        self.state = "progress"

        self.header = QLabel()
        self.header.setStyleSheet("font-weight: 600;")
        self.bar = QProgressBar()
        self.status = QLabel()
        self.pods = QTableWidget(0, 5)
        self.pods.setHorizontalHeaderLabels(["Pod", "Status", "Ready", "Restarts", "Issue"])
        self.pods.verticalHeader().hide()
        self.pods.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pods.setSelectionBehavior(QTableWidget.SelectRows)
        self.pods.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.events = QPlainTextEdit(readOnly=True)
        self.events.setMaximumHeight(70)
        self.events.setPlaceholderText("No warning events")
        lay = QVBoxLayout(self)
        for w in (self.header, self.bar, self.status, self.pods, self.events):
            lay.addWidget(w)
        self.restart(start, target)

    def restart(self, start, target):
        """(Re)start tracking, e.g. when the same workload is scaled again."""
        self._gen += 1
        self._stopped = False
        self.state = "progress"
        self.start, self.target = start, target
        self._t0 = time.monotonic()
        self.header.setText(f"Scaling {self.kind} {self.name}: {start} → {target} replicas")
        if target < start:
            self.bar.setRange(0, start - target)
            self.bar.setFormat("%v / %m removed")
        else:
            self.bar.setRange(0, max(target, 1))
            self.bar.setFormat("%v / %m ready")
        self.bar.setValue(0)
        self._set_status("progress", "Starting…")
        self._poll()

    def stop(self):
        self._stopped = True
        self._gen += 1  # drop any in-flight result

    def _poll(self):
        if self._stopped:
            return
        gen = self._gen
        self._run(lambda: self.service.scale_status(self.kind, self.name, self.namespace),
                  lambda st: self._on_status(gen, st), on_error=lambda m: self._on_error(gen, m))

    def _on_status(self, gen, st):
        if gen != self._gen:
            return
        elapsed = time.monotonic() - self._t0
        state, text = evaluate(self.target, st, elapsed, self.timeout_s)
        if self.target < self.start:  # scaling down: progress is pods actually gone
            total = self.start - self.target
            self.bar.setValue(min(max(self.start - len(st["pods"]), 0), total))
        else:
            self.bar.setValue(min(st["ready"], self.target))
        self._fill_pods(st["pods"])
        self.events.setPlainText("\n".join(st["warnings"]))
        self._set_status(state, text)
        self.polled.emit()
        if state == "progress":
            QTimer.singleShot(self.poll_ms, self._poll)
        else:
            self.state = state
            self.finished.emit(state, text)

    def _on_error(self, gen, msg):
        if gen != self._gen:
            return
        elapsed = time.monotonic() - self._t0
        if elapsed >= self.timeout_s:
            text = f"Timed out after {elapsed:.0f}s; last error: {msg}"
            self._set_status("timeout", text)
            self.state = "timeout"
            self.finished.emit("timeout", text)
            return
        self._set_status("progress", f"Could not read status ({msg}); retrying…")
        QTimer.singleShot(self.poll_ms, self._poll)

    def _set_status(self, state, text):
        color = COLORS[state]
        self.status.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")
        self.status.setText(text)

    def _fill_pods(self, pods):
        self.pods.setRowCount(len(pods))
        for r, p in enumerate(pods):
            vals = [p["name"], "Terminating" if p["deleting"] else p["status"], p["ready"], str(p["restarts"]), p["issue"]]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if p["issue"] and not p["deleting"]:
                    item.setForeground(QColor(COLORS["failed"]))
                self.pods.setItem(r, c, item)
