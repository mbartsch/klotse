"""Copy files or folders between this computer and a container, run by `kubectl cp` (the same kubectl Shell, Attach and
Port forward already need, with the explicit --context / --kubeconfig). One `kubectl` process per copy, started and stopped here;
the panel lists them. `kubectl cp` needs `tar` inside the container and prints no progress, so the panel shows the time taken and,
for a download, how many bytes have arrived so far; Cancel kills the local kubectl (a half-written file or folder is left as it is)."""
import os
import posixpath
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .terminal import kubectl_base

ACTIVE = ("running", "stopping")
STATE_TEXT = {"running": "… Copying", "stopping": "■ Cancelling", "done": "✔ Done", "cancelled": "■ Cancelled", "failed": "▲ Failed"}
UPLOAD, DOWNLOAD = "upload", "download"


def valid_remote(path):
    """The container path, trimmed and without trailing slashes; ValueError with a message when it is empty, has a control
    character, or is the root itself (copying `/` is never what is meant)."""
    path = str(path).strip()
    if not path:
        raise ValueError("Type the path inside the container.")
    if any(ord(ch) < 32 for ch in path):
        raise ValueError("The container path has a control character.")
    trimmed = path.rstrip("/")
    if not trimmed:
        raise ValueError("Copying the root directory '/' is not supported.")
    return trimmed


def local_size(path):
    """Bytes under `path` (a file, or everything in a folder); 0 when it is not there. Never raises: it only feeds a progress number."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for folder, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


@dataclass(eq=False)
class Copy:
    id: int
    context: str
    namespace: str
    pod: str
    container: str
    direction: str  # "upload" or "download"
    local: str  # absolute local file or folder
    remote: str
    state: str = "running"  # running | stopping | done | cancelled | failed
    message: str = ""
    started: float = field(default_factory=time.monotonic)
    ended: float = 0.0
    bytes_local: int = 0  # downloads: what is on disk so far
    process: object = field(default=None, repr=False)

    @property
    def running(self):
        return self.state in ACTIVE

    @property
    def elapsed(self):
        return (self.ended or time.monotonic()) - self.started

    @property
    def description(self):
        arrow = "→" if self.direction == UPLOAD else "←"
        return f"{self.local} {arrow} {self.pod}:{self.remote}"


def build_args(base, copy):
    """kubectl argv for a copy. The process runs in the local folder and names the local side relative to it ("./name"), because
    kubectl reads a `C:\\...` drive letter as `pod:` and a leading "-" as a flag; the container side is `pod:path` with -n/-c."""
    remote = f"{copy.pod}:{copy.remote}"
    local = "./" + os.path.basename(copy.local.rstrip("/\\"))
    args = base + ["-n", copy.namespace, "cp"] + (["-c", copy.container] if copy.container else [])
    return args + ([local, remote] if copy.direction == UPLOAD else [remote, local])


class FileCopies(QObject):
    """The copies of one cluster workspace. `base(context, kubeconfig)` gives the kubectl command start (tests replace it)."""
    changed = Signal()

    def __init__(self, base=kubectl_base, parent=None):
        super().__init__(parent)
        self.base = base
        self.items = []
        self._next = 1
        self._timer = QTimer(self)  # downloads: how much has arrived (a disk walk, so once a second, only while one runs)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def start(self, context, namespace, pod, container, direction, local, remote, kubeconfig=None):
        """Start `kubectl cp` and return its Copy. A bad remote path or a missing upload source raises ValueError."""
        remote = valid_remote(remote)
        local = os.path.abspath(local)
        if direction == UPLOAD and not os.path.exists(local):
            raise ValueError(f"{local} does not exist.")
        folder = os.path.dirname(local)
        if not os.path.isdir(folder):
            raise ValueError(f"The folder {folder} does not exist.")
        copy = Copy(self._next, context, namespace, pod, container or "", direction, local, remote)
        self._next += 1
        process = QProcess(self)
        base = self.base(context, kubeconfig)
        process.setProgram(base[0])
        process.setArguments(build_args(base, copy)[1:])
        process.setWorkingDirectory(folder)
        process.readyReadStandardError.connect(lambda: self._output(copy, process))
        process.readyReadStandardOutput.connect(lambda: process.readAllStandardOutput())  # nothing to show; do not let it pile up
        process.finished.connect(lambda code, _status: self._finished(copy, code))
        process.errorOccurred.connect(lambda error: self._failed_to_start(copy, error))
        copy.process = process
        self.items.append(copy)
        process.start()
        self._timer.start()
        self.changed.emit()
        return copy

    def _output(self, copy, process):
        data = bytes(process.readAllStandardError()).decode("utf-8", "replace")
        for line in data.splitlines():
            line = line.strip()
            if line and not line.startswith("Defaulted container") and not line.startswith("tar: Removing leading"):
                copy.message = line  # kubectl's own words are the explanation if it then fails

    def _finished(self, copy, code):
        if copy.state == "failed":
            return
        copy.ended = time.monotonic()
        if copy.state == "stopping":
            copy.state, copy.message = "cancelled", ""
        elif code == 0:
            copy.state, copy.message = "done", ""
        else:
            copy.state, copy.message = "failed", copy.message or f"kubectl stopped with exit code {code}."
        self._tick()
        self.changed.emit()

    def _failed_to_start(self, copy, error):
        if error == QProcess.FailedToStart:
            copy.state, copy.ended = "failed", time.monotonic()
            copy.message = "Cannot start kubectl: is it installed and on the PATH?"
            self.changed.emit()

    def _tick(self):
        for copy in self.items:
            if copy.direction == DOWNLOAD and (copy.running or copy.state == "done"):
                copy.bytes_local = local_size(copy.local)
        if not self.active():
            self._timer.stop()
        self.changed.emit()

    def cancel(self, copy):
        if copy.running:
            copy.state = "stopping"
            copy.process.kill()
            self.changed.emit()

    def cancel_all(self):
        for copy in list(self.items):
            self.cancel(copy)

    def remove_finished(self):
        self.items = [c for c in self.items if c.running]
        self.changed.emit()

    def active(self):
        return [c for c in self.items if c.running]


class FileCopyPanel(QWidget):
    """The list of copies (a tab in the Logs & activity dock). Closing the tab cancels the running ones."""
    key = "file-copies"
    HEADERS = ["Pod", "Namespace", "Direction", "Files", "State", "Time", "Received"]

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_all_button = QPushButton("Cancel all")
        self.clear_button = QPushButton("Clear finished")
        self.cancel_button.clicked.connect(lambda: self._selected() and manager.cancel(self._selected()))
        self.cancel_all_button.clicked.connect(manager.cancel_all)
        self.clear_button.clicked.connect(manager.remove_finished)
        self.note = QLabel("kubectl cp shows no progress of its own: the time is real, 'Received' is what a download has written so far. "
                           "The container needs tar. Closing this tab cancels running copies.")
        self.note.setWordWrap(True)
        theme.muted(self.note)
        buttons = QHBoxLayout()
        for button in (self.cancel_button, self.cancel_all_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        manager.changed.connect(self.refresh)
        self.refresh()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.manager.items[rows[0].row()] if rows and rows[0].row() < len(self.manager.items) else None

    def refresh(self):
        selected = self._selected()
        self.table.setRowCount(len(self.manager.items))
        for r, c in enumerate(self.manager.items):
            state = STATE_TEXT[c.state] + (f": {c.message}" if c.state == "failed" and c.message else "")
            received = human_size(c.bytes_local) if c.direction == DOWNLOAD else "—"
            values = [c.pod, c.namespace, "Upload" if c.direction == UPLOAD else "Download", c.description, state,
                      f"{int(c.elapsed)} s", received]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, col, item)
        if selected in self.manager.items:
            self.table.selectRow(self.manager.items.index(selected))
        self.table.resizeColumnsToContents()
        self._update_buttons()

    def _update_buttons(self, *_):
        c = self._selected()
        self.cancel_button.setEnabled(c is not None and c.running)
        self.cancel_all_button.setEnabled(bool(self.manager.active()))
        self.clear_button.setEnabled(any(not c.running for c in self.manager.items))

    def stop(self):
        """Called when the tab is closed or the cluster is: cancel every running copy."""
        self.manager.cancel_all()


class FileCopyDialog(QDialog):
    """Direction, container, the local file or folder and the path in the container. `containers` are the pod's container names."""

    def __init__(self, pod, namespace, containers, parent=None, direction=DOWNLOAD, container=None, remote="", browse=None):
        super().__init__(parent)
        self.browse = browse  # fn(container, typed path) -> (container, path) or None: the container file browser
        self.setWindowTitle(f"Copy files: {pod}")
        self.download = QRadioButton("Download: container → this computer")
        self.upload = QRadioButton("Upload: this computer → container")
        (self.upload if direction == UPLOAD else self.download).setChecked(True)
        self.container = QComboBox()
        self.container.addItems(list(containers))
        if container:
            self.container.setCurrentText(container)
        self.remote = QLineEdit(remote, placeholderText="path in the container, e.g. /var/log/app.log")
        self.browse_remote = QPushButton("Browse…")
        self.browse_remote.setToolTip("Pick the path from the files of the container (you can also just type it)")
        self.browse_remote.setVisible(browse is not None)
        self.browse_remote.clicked.connect(self._browse_remote)
        self.local = QLineEdit(placeholderText="file or folder on this computer")
        self.browse_file = QPushButton("File…")
        self.browse_folder = QPushButton("Folder…")
        self.browse_file.clicked.connect(self._pick_file)
        self.browse_folder.clicked.connect(self._pick_folder)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(theme.warning_style())
        self.note.hide()
        hint = QLabel("Uses kubectl cp, which needs tar in the container. Download: the local path is where the copy is written "
                      "(its folder must exist). Upload: the container path is the exact target; an existing file there is replaced.")
        hint.setWordWrap(True)
        theme.muted(hint)
        header = QLabel(f"Pod <b>{_plain(namespace)}/{_plain(pod)}</b>")
        header.setTextFormat(Qt.RichText)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local, 1)
        local_row.addWidget(self.browse_file)
        local_row.addWidget(self.browse_folder)
        form = QFormLayout()
        form.addRow("Container", self.container)
        remote_row = QHBoxLayout()
        remote_row.addWidget(self.remote, 1)
        remote_row.addWidget(self.browse_remote)
        form.addRow("Container path", remote_row)
        form.addRow("This computer", local_row)
        start = QPushButton("Copy")
        start.setDefault(True)
        start.clicked.connect(self._accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(start)
        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.download)
        layout.addWidget(self.upload)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.note)
        layout.addLayout(buttons)

    def direction(self):
        return UPLOAD if self.upload.isChecked() else DOWNLOAD

    def _browse_remote(self):
        picked = self.browse(self.container.currentText(), self.remote.text().strip()) if self.browse else None
        if picked:
            container, path = picked
            if container:
                self.container.setCurrentText(container)
            self.remote.setText(path)

    def _pick_file(self):
        if self.direction() == UPLOAD:
            path, _ = QFileDialog.getOpenFileName(self, "File to upload")
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Save as")
        if path:
            self.local.setText(path)

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Folder")
        if path:
            self.local.setText(path)

    def values(self):
        """(direction, container, local path, remote path); ValueError with a message for a bad entry."""
        remote = valid_remote(self.remote.text())
        local = self.local.text().strip()
        if not local:
            raise ValueError("Choose the file or folder on this computer.")
        return self.direction(), self.container.currentText(), local, remote

    def _accept(self):
        try:
            self.result_values = self.values()
        except ValueError as e:
            self.note.setText(str(e))
            self.note.show()
            return
        self.accept()


def _plain(text):
    from html import escape
    return escape(str(text))
