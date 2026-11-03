import codecs
import shlex

from PySide6.QtCore import QProcess, Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class CommandView(QWidget):
    """Stream a CLI operation into the activity dock; stop its process when the view closes."""

    completed = Signal(bool)

    def __init__(self, argv, parent=None):
        super().__init__(parent)
        self.argv = argv
        self._done = False
        self._stopping = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setMaximumBlockCount(20000)
        self.text.setPlainText(shlex.join(argv) + "\n\n")
        self.stop_button = QPushButton("Stop drain")
        self.stop_button.clicked.connect(self.stop)
        self.status = QLabel("Draining… The node stays cordoned after completion, failure or stop.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.text)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self):
        self.process.start(self.argv[0], self.argv[1:])

    def _append(self, text):
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)

    def _read(self):
        self._append(self._decoder.decode(bytes(self.process.readAllStandardOutput())))

    def _finish(self, success, status):
        if self._done:
            return
        self._done = True
        self._read()
        self._append(self._decoder.decode(b"", final=True))
        self.status.setText(status + " The node remains cordoned if cordon succeeded.")
        self.stop_button.setEnabled(False)
        self.completed.emit(success)

    def _finished(self, code, status):
        success = code == 0 and status == QProcess.NormalExit and not self._stopping
        self._finish(success, "Drain stopped." if self._stopping else
                     "Drain completed." if success else f"Drain failed (exit {code}).")

    def _error(self, error):
        self._append(f"\n{self.process.errorString()}\n")
        if error == QProcess.FailedToStart:
            self._finish(False, "Could not start drain.")

    def stop(self):
        if not self._done and self.process.state() != QProcess.NotRunning:
            self._stopping = True
            self.process.kill()
            self.process.waitForFinished(1000)
