"""Export diagnostics…: a zip with the manifests, events, node conditions and pod logs of a namespace (see k8s/diagnostics.py). It runs on
its own thread with progress and a Cancel button. Read-only; Secrets are never read."""
import os
import threading
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout)

from ..k8s import diagnostics
from . import theme


class DiagnosticsJob(QThread):
    progress = Signal(str)
    done = Signal(object)  # the result dict of diagnostics.collect
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, service, context, namespace, path, options, version=""):
        super().__init__()
        self.args = (service, context, namespace, path, options, version)
        self.cancel = threading.Event()

    def run(self):
        service, context, namespace, path, options, version = self.args
        try:
            result = diagnostics.collect(service, context, namespace, path, options, version, self.progress.emit, self.cancel)
        except diagnostics.Cancelled:
            self.cancelled.emit()
        except Exception as error:  # noqa: BLE001 - shown to the user, not swallowed
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.done.emit(result)


class DiagnosticsDialog(QDialog):
    def __init__(self, service, context, namespace, version="", parent=None):
        super().__init__(parent)
        self.service, self.context, self.namespace, self.version = service, context, namespace, version
        self.job = None
        self.setWindowTitle("Export diagnostics")
        self.resize(560, 360)
        header = QLabel(f"<b>{_plain(context)}</b> · namespace <b>{_plain(namespace)}</b>")
        header.setTextFormat(Qt.RichText)
        self.logs = QCheckBox("Include pod logs")
        self.logs.setChecked(True)
        self.tail = QSpinBox()
        self.tail.setRange(10, 100000)
        self.tail.setValue(500)
        self.tail.setSuffix(" lines per container")
        self.keep_env = QCheckBox("Keep environment variable values (they are replaced by <redacted> otherwise)")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"), f"klotse-diagnostics-{namespace}-{stamp}.zip"))
        self.choose = QPushButton("Choose…")
        self.choose.clicked.connect(self._choose)
        warning = QLabel("Secrets are never read and ConfigMap values are left out. Pod logs are written exactly as the containers wrote "
                         "them, and command lines are not rewritten: look inside before you share the file.")
        warning.setWordWrap(True)
        warning.setStyleSheet(theme.warning_style())
        self.status = QLabel("")
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.start_button = QPushButton("Export")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel export")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        form = QFormLayout()
        form.addRow(self.logs)
        form.addRow("Tail", self.tail)
        form.addRow(self.keep_env)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.choose)
        form.addRow("Save to", path_row)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(header)
        lay.addLayout(form)
        lay.addWidget(warning)
        lay.addWidget(self.status, 1)
        lay.addLayout(buttons)
        self.logs.toggled.connect(self.tail.setEnabled)

    def _choose(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostics", self.path.text(), "Zip files (*.zip)")
        if path:
            self.path.setText(path if path.lower().endswith(".zip") else path + ".zip")

    def options(self):
        return {"logs": self.logs.isChecked(), "tail": self.tail.value(), "keep_env": self.keep_env.isChecked()}

    def start(self):
        path = self.path.text().strip()
        if not path:
            self.status.setText("Choose where to save the zip file.")
            return None
        if os.path.exists(path) and QMessageBox.question(self, "Replace file", f"{path} already exists. Replace it?",
                                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return None
        self.job = DiagnosticsJob(self.service, self.context, self.namespace, path, self.options(), self.version)
        self.job.progress.connect(self.status.setText)
        self.job.done.connect(self._done)
        self.job.failed.connect(self._failed)
        self.job.cancelled.connect(self._cancelled)
        self._busy(True)
        self.status.setText("Starting…")
        self.job.start()
        return self.job

    def cancel(self):
        if self.job is not None:
            self.job.cancel.set()
            self.status.setText("Cancelling…")

    def _busy(self, busy):
        self.start_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def _remove_partial(self):
        try:
            os.remove(self.path.text().strip())
        except OSError:
            pass

    def _done(self, result):
        self._busy(False)
        counts = ", ".join(f"{k} {v}" for k, v in result["counts"].items())
        text = f"Saved {self.path.text().strip()}\n{counts}."
        if result["notes"]:
            text += "\nCould not read: " + "; ".join(result["notes"][:6]) + ("…" if len(result["notes"]) > 6 else "")
        self.status.setText(text)

    def _failed(self, message):
        self._busy(False)
        self._remove_partial()
        self.status.setText(f"Export failed: {message}")

    def _cancelled(self):
        self._busy(False)
        self._remove_partial()
        self.status.setText("Cancelled; the unfinished file was removed.")

    def reject(self):
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.job.wait(5000)
        super().reject()

    def closeEvent(self, event):
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.job.wait(5000)
        super().closeEvent(event)


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
