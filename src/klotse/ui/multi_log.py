"""Logs of many pods at once, like `stern`: the pods of a Deployment (or any workload or Service with a selector), of a label
selector typed by hand, or of pods picked in a list. Every line is prefixed with `[pod/container]`, a regular expression keeps
or drops lines, and what is shown can be saved. Looking only."""
import re
from collections import deque

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..k8s.selectors import valid_selector
from . import theme
from .log_view import LogStreamer, _alive

STREAM_END = "\n[stream ended]\n"  # what LogStreamer emits when a log stream closes by itself
MAX_LINES = 50000  # kept in memory; older lines fall off
MAX_STREAMS = 40  # containers followed at once
MAX_LINE_MATCH = 4000  # a regular expression looks at no more than this many characters of a line
RESOLVE_MS = 10000  # while following, look again for new pods this often
SINCE = [("everything the pods still have", 0), ("last 5 minutes", 300), ("last 15 minutes", 900), ("last hour", 3600),
         ("last 6 hours", 21600), ("last 24 hours", 86400)]


class MultiLogView(QWidget):
    """`resolver(selector_text)` returns [(pod, [containers])] (called off the GUI thread through `run(fn, on_result, on_error=)`);
    for pods picked by hand the selector box is hidden and the resolver ignores it. `service.open_log_stream` opens one stream."""

    changed = Signal()

    def __init__(self, service, namespace, resolver, run, selector="", fixed=False, parent=None):
        super().__init__(parent)
        self.service, self.namespace, self.resolver, self.run, self.fixed = service, namespace, resolver, run, fixed
        self.lines = deque(maxlen=MAX_LINES)  # every line received
        self.streams, self.buffers, self.multi = {}, {}, set()  # (pod, container) -> streamer / partial line / pods with >1 container
        self._regex = self._exclude = None
        self._resolving = False
        self._closed = False

        self.selector = QLineEdit(selector, placeholderText="label selector, e.g. app=web,tier=frontend (Enter to apply)")
        self.selector.setVisible(not fixed)
        self.container = QComboBox()
        self.container.addItem("All containers", "")
        self.since = QComboBox()
        for label, seconds in SINCE:
            self.since.addItem(label, seconds)
        self.tail = QSpinBox()
        self.tail.setRange(0, 100000)
        self.tail.setValue(200)
        self.tail.setSpecialValueText("all")
        self.tail.setSuffix(" lines per container")
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        self.timestamps = QCheckBox("Timestamps")
        self.match = QLineEdit(placeholderText="show only lines matching (regular expression)", clearButtonEnabled=True)
        self.drop = QLineEdit(placeholderText="hide lines matching", clearButtonEnabled=True)
        self.ignore_case = QCheckBox("Ignore case")
        self.ignore_case.setChecked(True)
        self.restart_button = QPushButton("Restart")
        self.clear_button = QPushButton("Clear")
        self.save_button = QPushButton("Save…")
        self.status = QLabel("Looking for pods…")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(MAX_LINES)

        first = QHBoxLayout()
        first.setContentsMargins(6, 4, 6, 0)
        for w in (self.container, self.since, self.tail, self.follow, self.timestamps):
            first.addWidget(w)
        first.addStretch(1)
        second = QHBoxLayout()
        second.setContentsMargins(6, 0, 6, 0)
        second.addWidget(self.match, 2)
        second.addWidget(self.drop, 1)
        second.addWidget(self.ignore_case)
        for w in (self.restart_button, self.clear_button, self.save_button):
            second.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.selector)
        lay.addLayout(first)
        lay.addLayout(second)
        lay.addWidget(self.status)
        lay.addWidget(self.text, 1)

        self._filter_timer = QTimer(self, interval=300, singleShot=True)
        self._filter_timer.timeout.connect(self.refilter)
        self.match.textChanged.connect(lambda _: self._filter_timer.start())
        self.drop.textChanged.connect(lambda _: self._filter_timer.start())
        self.ignore_case.toggled.connect(lambda _: self._filter_timer.start())
        self.selector.returnPressed.connect(self.start)
        self.container.currentIndexChanged.connect(lambda _: self.start())
        self.since.currentIndexChanged.connect(lambda _: self.start())
        self.tail.editingFinished.connect(self.start)
        self.timestamps.toggled.connect(lambda _: self.start())
        self.restart_button.clicked.connect(self.start)
        self.clear_button.clicked.connect(self.clear)
        self.save_button.clicked.connect(self.save)
        self._poll = QTimer(self, interval=RESOLVE_MS)
        self._poll.timeout.connect(self._poll_pods)
        self._poll.start()
        self.start()

    # ---- finding the pods ----------------------------------------------------------------------------------------
    def start(self):
        """(Re)start: stop the streams, forget the lines, look for the pods and open a stream per container."""
        self._stop_streams()
        self.lines.clear()
        self.text.clear()
        problem = None if self.fixed else valid_selector(self.selector.text())
        if problem:
            self.status.setText(problem)
            return
        self._resolve(first=True)

    def _poll_pods(self):
        if self.follow.isChecked() and not self._closed and (self.streams or not self.fixed):
            self._resolve(first=False)

    def _resolve(self, first):
        if self._resolving or self._closed:
            return
        self._resolving = True
        text = self.selector.text().strip()
        self.run(lambda: self.resolver(text), lambda pods: self._found(pods, first), on_error=self._resolve_failed)

    def _found(self, pods, first):
        self._resolving = False
        if self._closed:
            return
        wanted = self.container.currentData()
        names = sorted({c for _, containers in pods for c in containers})
        self._offer_containers(names)
        self.multi = {pod for pod, containers in pods if len(containers) > 1}
        keys = [(pod, c) for pod, containers in pods for c in containers if not wanted or c == wanted]
        fresh = [k for k in keys if k not in self.streams]
        room = max(0, MAX_STREAMS - len(self.streams))
        for key in fresh[:room]:
            self._open(key)
        cut = len(fresh) - room if len(fresh) > room else 0
        self.status.setText(self._summary(len({p for p, _ in self.streams}), len(keys), cut, bool(pods)))
        self.changed.emit()

    def _resolve_failed(self, message):
        self._resolving = False
        self.status.setText(f"Could not list the pods: {message}")

    def _offer_containers(self, names):
        keep = self.container.currentData()
        have = [self.container.itemText(i) for i in range(1, self.container.count())]
        if names != have:
            blocker = self.container.blockSignals(True)
            self.container.clear()
            self.container.addItem("All containers", "")
            for name in names:
                self.container.addItem(name, name)
            index = self.container.findData(keep)
            self.container.setCurrentIndex(max(index, 0))
            self.container.blockSignals(blocker)

    def _summary(self, pods, containers, cut, any_pods):
        if not any_pods:
            return "No pods match." if not self.fixed else "No pods."
        text = f"{pods} pod{'s' if pods != 1 else ''}, {containers} container{'s' if containers != 1 else ''}"
        if cut:
            text += f" (only the first {MAX_STREAMS} are followed; narrow the selector or pick a container)"
        return text + ". Lines from different pods are in the order they arrive."

    # ---- streams -------------------------------------------------------------------------------------------------
    def _open(self, key):
        pod, container = key
        extra = {}
        if self.timestamps.isChecked():
            extra["timestamps"] = True
        seconds = self.since.currentData()
        if seconds:
            extra["since_seconds"] = seconds
        tail = self.tail.value() or None
        if not self.follow.isChecked():
            extra["follow"] = False
        namespace = self.namespace

        def opener():
            return self.service.open_log_stream(namespace, pod, container, tail, **extra)
        streamer = LogStreamer(opener)
        streamer.chunk.connect(lambda text, k=key: self._chunk(k, text))
        streamer.failed.connect(lambda message, k=key: self._add_line(k, f"[error] {message}"))
        _alive.add(streamer)
        streamer.finished.connect(lambda s=streamer, k=key: (_alive.discard(s), self._flush(k)))
        self.streams[key] = streamer
        self.buffers[key] = ""
        streamer.start()

    def _chunk(self, key, text):
        if self._closed:
            return
        if text == STREAM_END:  # the streamer's own marker: keep it as one line, without the blank line before it
            self._flush(key)
            self.buffers[key] = ""
            self._add_line(key, "[stream ended]")
            return
        data = self.buffers.get(key, "") + text
        *complete, rest = data.split("\n")
        self.buffers[key] = rest
        for line in complete:
            self._add_line(key, line.rstrip("\r"))

    def _flush(self, key):
        rest = self.buffers.pop(key, "")
        if rest and not self._closed:
            self._add_line(key, rest)

    def prefix(self, key):
        pod, container = key
        return f"[{pod}/{container}] " if pod in self.multi else f"[{pod}] "

    def _add_line(self, key, line):
        full = self.prefix(key) + line
        self.lines.append(full)
        if self._shown(full):
            self._append(full)

    def _append(self, line):
        cursor = QTextCursor(self.text.document())  # separate cursor: leaves the user's selection alone
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(line + "\n")
        if self.follow.isChecked():
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _stop_streams(self):
        streams, self.streams, self.buffers = self.streams, {}, {}
        for streamer in streams.values():
            try:
                streamer.chunk.disconnect()
                streamer.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            streamer.stop()

    def stop(self):
        """Close the view for good (the workspace calls this when the tab or the cluster closes): no stream, no new pod lookup."""
        self._closed = True
        self._poll.stop()
        self._stop_streams()

    # ---- filtering, clearing, saving -------------------------------------------------------------------------------
    def _compile(self):
        flags = re.IGNORECASE if self.ignore_case.isChecked() else 0
        compiled = []
        for box in (self.match, self.drop):
            text = box.text()
            if not text:
                compiled.append(None)
                continue
            try:
                compiled.append(re.compile(text, flags))
                box.setStyleSheet("")
            except re.error as error:
                box.setStyleSheet(theme.warning_style())
                box.setToolTip(f"Not a valid regular expression: {error}")
                compiled.append(None)  # a bad expression filters nothing until it is fixed
        self._regex, self._exclude = compiled

    def _shown(self, line):
        text = line[:MAX_LINE_MATCH]
        if self._regex is not None and not self._regex.search(text):
            return False
        return not (self._exclude is not None and self._exclude.search(text))

    def refilter(self):
        """Show again only the lines that pass the current expressions (all lines received are kept)."""
        self._compile()
        shown = [line for line in self.lines if self._shown(line)]
        self.text.setPlainText("\n".join(shown) + ("\n" if shown else ""))
        if self.follow.isChecked():
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def clear(self):
        self.lines.clear()
        self.text.clear()

    def visible_text(self):
        return self.text.toPlainText()

    def save(self):
        """Write the lines on screen to a text file chosen by the user."""
        path, _ = QFileDialog.getSaveFileName(self, "Save logs", "logs.txt", "Text files (*.txt);;All files (*)")
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self.visible_text())
        except OSError as error:
            QMessageBox.warning(self, "Save logs", str(error))
            return None
        self.status.setText(f"Saved to {path}")
        return path
