import codecs
import socket

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

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
        self.container.addItems(containers)
        if initial_container:
            self.container.setCurrentText(initial_container)
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        self.tail = QSpinBox()
        self.tail.setRange(10, 100000)
        self.tail.setSingleStep(100)
        self.tail.setValue(500)
        self.search = QLineEdit(placeholderText="Search in logs (Enter = next)", clearButtonEnabled=True)
        clear = QPushButton("Clear")
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(20000)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        for w in (QLabel("Container"), self.container, QLabel("Tail"), self.tail, self.follow):
            bar.addWidget(w)
        bar.addWidget(self.search, 1)
        bar.addWidget(clear)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.text)

        clear.clicked.connect(self.text.clear)
        self.search.returnPressed.connect(self.find_next)
        self.container.currentTextChanged.connect(lambda _: self.start())
        self.tail.editingFinished.connect(self.start)
        self.start()

    def start(self):
        self.stop()
        self.text.clear()
        name = self.container.currentText() or None
        tail = self.tail.value()
        s = LogStreamer(lambda: self.service.open_log_stream(self.namespace, self.pod, name, tail))
        s.chunk.connect(self._append)
        s.failed.connect(lambda m: self._append(f"\n[error] {m}\n"))
        _alive.add(s)
        s.finished.connect(lambda: _alive.discard(s))
        self._streamer = s
        s.start()

    def stop(self):
        s, self._streamer = self._streamer, None
        if s is not None:
            s.chunk.disconnect()
            s.failed.disconnect()
            s.stop()

    def _append(self, text):
        cur = QTextCursor(self.text.document())  # separate cursor: leaves the user's selection alone
        cur.movePosition(QTextCursor.End)
        cur.insertText(text)
        if self.follow.isChecked():
            bar = self.text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def find_next(self):
        term = self.search.text()
        if term and not self.text.find(term):
            self.text.moveCursor(QTextCursor.Start)  # wrap around
            self.text.find(term)
