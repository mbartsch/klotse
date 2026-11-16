import codecs
import socket

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from .log_toolbar import compile_filters, find_next, line_shown, save_visible

RAW_KEPT = 20000  # chunks of stream text kept so a filter can show them again (the same bound as the lines on screen)

_alive = set()  # streamers still unwinding after stop(); keeps QThread objects from being collected mid-run


class LogStreamer(QThread):
    """Reads a streaming HTTP response on its own thread and emits decoded text chunks."""

    chunk = Signal(str)
    failed = Signal(str)

    def __init__(self, opener):
        super().__init__()
        self._opener = opener
        self._stopped = False
        self._resp = None

    def run(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            self._resp = self._opener()
            if self._stopped:  # stop() arrived while the connection was still opening
                return
            for data in self._resp.stream(1024):
                if self._stopped:
                    return
                text = decoder.decode(data)
                if text:
                    self.chunk.emit(text)
            if not self._stopped:
                self.chunk.emit("\n[stream ended]\n")
        except Exception as e:
            if not self._stopped:
                self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            try:
                if self._resp is not None:
                    self._resp.close()
            except Exception:
                pass

    def stop(self):
        """Non-blocking: flags the thread and shuts the socket down to unblock a pending read.

        Never call resp.close() here: it contends with the reader's lock and would freeze the GUI thread;
        the reader thread closes the response itself in run()'s finally.
        """
        self._stopped = True
        sock = getattr(getattr(self._resp, "connection", None), "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def running_streamers():
    """The log streamers that have not finished yet."""
    return list(_alive)


def shutdown_all(timeout_ms=2000):
    for s in list(_alive):
        s.stop()
        s.wait(timeout_ms)


class LogView(QWidget):
    def __init__(self, service, namespace, pod, containers, parent=None, initial_container=None):
        super().__init__(parent)
        self.service, self.namespace, self.pod = service, namespace, pod
        self._streamer = None

        self.container = QComboBox()
        self.container.setAccessibleName(f"Container for logs from {pod}")
        self.container.addItems(containers)
        if initial_container:
            self.container.setCurrentText(initial_container)
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        self.wrap = QCheckBox("Wrap")
        self.timestamps = QCheckBox("Timestamps")
        self.previous = QCheckBox("Previous instance")
        self.previous.setToolTip("Show the log of the container's previous run (for example after a crash)")
        self.tail = QSpinBox()
        self.tail.setAccessibleName("Number of log lines to load")
        self.tail.setRange(0, 100000)
        self.tail.setSingleStep(100)
        self.tail.setValue(500)
        self.tail.setSpecialValueText("all")  # 0 = every line, as for several pods (#500)
        self.match = QLineEdit(placeholderText="show only lines matching (regular expression)", clearButtonEnabled=True)
        self.match.setAccessibleName(f"Show log lines matching a regular expression for pod {pod}")
        self.drop = QLineEdit(placeholderText="hide lines matching", clearButtonEnabled=True)
        self.drop.setAccessibleName(f"Hide log lines matching a regular expression for pod {pod}")
        self.ignore_case = QCheckBox("Ignore case")
        self.ignore_case.setAccessibleName(f"Ignore case in log filters for pod {pod}")
        self.ignore_case.setChecked(True)
        self.restart_button = QPushButton("Restart")
        self.restart_button.setAccessibleName(f"Restart logs for pod {pod}")
        self.save_button = QPushButton("Save…")
        self.save_button.setAccessibleName(f"Save visible logs for pod {pod}")
        self._raw = []  # the stream text as it arrived, so a filter can show it again
        self._regex = self._exclude = None
        self._filter_timer = QTimer(self, interval=300, singleShot=True)
        self._filter_timer.timeout.connect(self.refilter)
        self.search = QLineEdit(placeholderText="Find in logs (Enter = next)", clearButtonEnabled=True)
        self.search.setAccessibleName(f"Search logs for pod {pod}")
        clear = QPushButton("Clear")
        clear.setAccessibleName(f"Clear logs for pod {pod}")
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setAccessibleName(f"Logs for pod {pod}")
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(20000)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        for w in (QLabel("Container"), self.container, QLabel("Tail"), self.tail, self.follow, self.wrap, self.timestamps, self.previous):
            bar.addWidget(w)
        self.no_match = QLabel("No match")  # #497: Find said nothing when nothing matched
        self.no_match.hide()
        self.reconnect = QPushButton("Reconnect")  # shown when the stream has ended or failed
        self.reconnect.setAccessibleName(f"Reconnect logs for pod {pod}")
        self.reconnect.hide()
        self.drop_note = QLabel("")  # shown once when older lines start to fall off
        self.drop_note.setWordWrap(True)
        self.drop_note.hide()
        self._dropping = False
        bar.addWidget(self.search, 1)
        bar.addWidget(self.no_match)
        bar.addWidget(self.reconnect)
        bar.addWidget(clear)
        second = QHBoxLayout()
        second.setContentsMargins(6, 0, 6, 4)
        second.addWidget(self.match, 2)
        second.addWidget(self.drop, 1)
        second.addWidget(self.ignore_case)
        for w in (self.restart_button, self.save_button):
            second.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addLayout(second)
        lay.addWidget(self.drop_note)
        lay.addWidget(self.text)
        self.reconnect.clicked.connect(self.start)

        clear.clicked.connect(self.clear)
        self.restart_button.clicked.connect(self.start)
        self.save_button.clicked.connect(self.save)
        self.match.textChanged.connect(lambda _text: self._filter_timer.start())
        self.drop.textChanged.connect(lambda _text: self._filter_timer.start())
        self.ignore_case.toggled.connect(lambda _on: self._filter_timer.start())
        self.search.returnPressed.connect(self.find_next)
        self.search.textChanged.connect(lambda _text: self.no_match.hide())
        self.container.currentTextChanged.connect(lambda _: self.start())
        self.tail.editingFinished.connect(self.start)
        self.wrap.toggled.connect(self._set_wrap)
        self.timestamps.toggled.connect(lambda _: self.start())
        self.previous.toggled.connect(lambda _: self.start())
        self.start()

    def start(self):
        self.stop()
        self.text.clear()
        self._raw.clear()
        name = self.container.currentText() or None
        tail = self.tail.value() or None  # 0: every line
        extra = {}
        if self.timestamps.isChecked():
            extra["timestamps"] = True
        if self.previous.isChecked():
            extra["previous"] = True
        s = LogStreamer(lambda: self.service.open_log_stream(self.namespace, self.pod, name, tail, **extra))
        s.chunk.connect(self._append)
        s.failed.connect(lambda m: self._append(f"\n[error] {m}\n"))
        s.failed.connect(lambda _m: self.reconnect.show())
        self.reconnect.hide()
        _alive.add(s)
        s.finished.connect(lambda: _alive.discard(s))
        self._streamer = s
        s.start()

    def _set_wrap(self, on):
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    def stop(self):
        s, self._streamer = self._streamer, None
        if s is not None:
            s.chunk.disconnect()
            s.failed.disconnect()
            s.stop()

    def _append(self, text):
        self._raw.append(text)
        if len(self._raw) > RAW_KEPT:
            del self._raw[:len(self._raw) - RAW_KEPT]
        if self.match.text() or self.drop.text():
            self.refilter()  # only the lines that pass the filters are on screen
        else:
            self._show(text)
        if text == "\n[stream ended]\n":
            self.reconnect.show()

    def _show(self, text):
        cur = QTextCursor(self.text.document())  # separate cursor: leaves the user's selection alone
        cur.movePosition(QTextCursor.End)
        cur.insertText(text)
        if not self._dropping and self.text.document().blockCount() >= self.text.maximumBlockCount():
            self._dropping = True
            self.drop_note.setText(f"Only the newest {self.text.maximumBlockCount():,} lines are kept here; Save keeps the rest.")
            self.drop_note.show()
        if self.follow.isChecked():
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def refilter(self):
        """Show again only the lines that pass the current filters (all the stream text received is kept)."""
        self._regex, self._exclude = compile_filters(self.match, self.drop, self.ignore_case, self.drop_note)
        self.drop_note.hide()
        shown = [line for line in "".join(self._raw).splitlines(True)
                 if line_shown(line.rstrip("\r\n"), self._regex, self._exclude)]
        self.text.setPlainText("".join(shown))
        if self.follow.isChecked():
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def clear(self):
        self._raw.clear()
        self.text.clear()
        self._dropping = False

    def save(self):
        """Write the lines on screen to a file the user picks."""
        path = save_visible(self, self.text.toPlainText(), self.drop_note)
        if path:
            self.drop_note.show()

    def find_next(self):
        find_next(self.text, self.search.text(), self.no_match)
