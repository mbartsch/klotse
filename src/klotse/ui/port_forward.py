"""Port-forward from a pod or a service to this computer, run by `kubectl port-forward` (the same kubectl Shell, Attach and Drain
already need, with the explicit --context / --kubeconfig). One `kubectl` process per forward, started and stopped here; the
panel lists them and the dialog asks which ports. A forward listens on 127.0.0.1 only."""
import re
import webbrowser
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .terminal import kubectl_base

FORWARDING = re.compile(r"Forwarding from 127\.0\.0\.1:(\d+) -> (\d+)")
HANDLING = re.compile(r"Handling connection for (\d+)")
ACTIVE = ("starting", "active")
STATE_TEXT = {"starting": "… Starting", "active": "● Active", "stopping": "■ Stopping", "stopped": "■ Stopped", "failed": "▲ Failed"}


def valid_port(text, allow_empty=False):
    """The port number in `text` (1-65535); 0 for an empty text when allow_empty; ValueError with a message otherwise."""
    text = str(text).strip()
    if not text and allow_empty:
        return 0
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ValueError(f"'{text}' is not a port number (1-65535).")
    return int(text)


def forward_ports(kind, raw):
    """[(label, port)] a pod or service offers, for the dialog's list: a pod's container ports, a service's ports."""
    seen, found = set(), []
    spec = (raw or {}).get("spec") or {}
    if kind == "Pod":
        for container in spec.get("containers") or []:
            for p in container.get("ports") or []:
                number = p.get("containerPort")
                if isinstance(number, int) and number not in seen:
                    seen.add(number)
                    found.append((f"{p.get('name') or container.get('name', 'port')} ({number}/{p.get('protocol', 'TCP')})", number))
    elif kind == "Service":
        for p in spec.get("ports") or []:
            number = p.get("port")
            if isinstance(number, int) and number not in seen:
                seen.add(number)
                found.append((f"{p.get('name') or number} ({number} → {p.get('targetPort', number)})", number))
    return found


@dataclass(eq=False)
class Forward:
    id: int
    context: str
    namespace: str
    kind: str  # "Pod" or "Service"
    name: str
    remote: int
    requested: int  # the local port asked for; 0 = let kubectl pick a free one
    local: int = 0  # the port actually listening (known once kubectl says so)
    state: str = "starting"  # starting | active | stopping | stopped | failed
    message: str = ""
    connections: int = 0
    process: object = field(default=None, repr=False)

    @property
    def target(self):
        return f"{'pod' if self.kind == 'Pod' else 'svc'}/{self.name}"

    @property
    def address(self):
        return f"127.0.0.1:{self.local}" if self.local else ""

    @property
    def running(self):
        return self.state in ACTIVE


class PortForwards(QObject):
    """The forwards of one cluster workspace. `base(context, kubeconfig)` gives the kubectl command start (tests replace it)."""
    changed = Signal()

    def __init__(self, base=kubectl_base, parent=None):
        super().__init__(parent)
        self.base = base
        self.items = []
        self._next = 1

    def start(self, context, namespace, kind, name, remote, local=0, kubeconfig=None):
        """Start `kubectl port-forward` and return its Forward. The state turns Active when kubectl reports the local port, or
        Failed with kubectl's own message (port in use, pod gone, no permission, kubectl missing)."""
        remote = valid_port(remote)
        local = valid_port(local, allow_empty=True) if local else 0  # 0 or empty: kubectl picks a free port
        fw = Forward(self._next, context, namespace, kind, name, remote, local)
        self._next += 1
        base = self.base(context, kubeconfig)
        process = QProcess(self)
        process.setProgram(base[0])
        process.setArguments(base[1:] + ["-n", namespace, "port-forward", fw.target, f"{local or ''}:{remote}", "--address", "127.0.0.1"])
        process.readyReadStandardOutput.connect(lambda: self._output(fw, process, error=False))
        process.readyReadStandardError.connect(lambda: self._output(fw, process, error=True))
        process.finished.connect(lambda code, _status: self._finished(fw, code))
        process.errorOccurred.connect(lambda error: self._failed_to_start(fw, error))
        fw.process = process
        self.items.append(fw)
        process.start()
        self.changed.emit()
        return fw

    # ---- what kubectl says ---------------------------------------------------------------------------------------
    def _output(self, fw, process, error):
        data = bytes(process.readAllStandardError() if error else process.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            line = line.strip()
            found = FORWARDING.search(line)
            if found and fw.state in ("starting", "active"):
                fw.local, fw.state, fw.message = int(found.group(1)), "active", ""
            elif HANDLING.search(line):
                fw.connections += 1
            elif line and (error or fw.state == "starting"):
                fw.message = line  # kubectl's own words are the explanation if it then fails
        self.changed.emit()

    def _finished(self, fw, code):
        if fw.state == "failed":
            return
        if fw.state == "stopping" or code == 0:
            fw.state, fw.message = "stopped", ""
        else:
            fw.state, fw.message = "failed", fw.message or f"kubectl stopped with exit code {code}."
        self.changed.emit()

    def _failed_to_start(self, fw, error):
        if error == QProcess.FailedToStart:
            fw.state, fw.message = "failed", "Cannot start kubectl: is it installed and on the PATH?"
            self.changed.emit()

    # ---- stopping ------------------------------------------------------------------------------------------------
    def stop(self, fw):
        if fw.running:
            fw.state = "stopping"
            fw.process.kill()  # kubectl keeps no state worth a graceful goodbye, and terminate() does nothing on Windows
            self.changed.emit()

    def stop_all(self):
        for fw in list(self.items):
            self.stop(fw)

    def remove_finished(self):
        self.items = [fw for fw in self.items if fw.running or fw.state == "stopping"]
        self.changed.emit()

    def active(self):
        return [fw for fw in self.items if fw.running]


class PortForwardPanel(QWidget):
    """The list of forwards (a tab in the Logs & activity dock). Closing the tab stops them all, so none is left running
    unseen."""
    key = "port-forwards"
    HEADERS = ["Target", "Namespace", "Local address", "Remote port", "State", "Connections"]

    def __init__(self, manager, parent=None, saved=None, context="", start=None):
        super().__init__(parent)
        self.manager = manager
        self.saved, self.context, self._start = saved, context, start  # saved: SavedForwards; start(entry) begins one
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.stop_button = QPushButton("Stop")
        self.stop_all_button = QPushButton("Stop all")
        self.copy_button = QPushButton("Copy address")
        self.open_button = QPushButton("Open in browser")
        self.open_button.setToolTip("Open http://127.0.0.1:<port> (only useful when the port speaks HTTP)")
        self.clear_button = QPushButton("Clear finished")
        self.stop_button.clicked.connect(lambda: self._selected() and manager.stop(self._selected()))
        self.stop_all_button.clicked.connect(manager.stop_all)
        self.copy_button.clicked.connect(self._copy)
        self.open_button.clicked.connect(self._open)
        self.clear_button.clicked.connect(manager.remove_finished)
        self.note = QLabel("Forwards listen on 127.0.0.1 only. Closing this tab stops all of them.")
        theme.muted(self.note)
        buttons = QHBoxLayout()
        for button in (self.stop_button, self.stop_all_button, self.copy_button, self.open_button, self.clear_button):
            buttons.addWidget(button)
        self.saved_button = QToolButton()
        self.saved_button.setText("Saved forwards ▾")
        self.saved_button.setPopupMode(QToolButton.InstantPopup)
        self.saved_menu = QMenu(self.saved_button)
        self.saved_menu.aboutToShow.connect(self._fill_saved)
        self.saved_button.setMenu(self.saved_menu)
        self.saved_button.setVisible(saved is not None)
        buttons.addWidget(self.saved_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.note)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        manager.changed.connect(self.refresh)
        self.refresh()

    def _fill_saved(self):
        """The forwards kept for this cluster: choosing one starts it; Forget removes it from the list."""
        menu = self.saved_menu
        menu.clear()
        entries = self.saved.for_context(self.context) if self.saved is not None else []
        if not entries:
            menu.addAction("No saved forwards (tick “Remember this forward” when starting one)").setEnabled(False)
            return
        for entry in entries:
            local = f" → local {entry['local']}" if entry["local"] else ""
            menu.addAction(f"Start {entry['kind'].lower()} {entry['ns']}/{entry['name']} port {entry['remote']}{local}",
                           lambda e=entry: self._start and self._start(e))
        forget = menu.addMenu("Forget")
        for entry in entries:
            forget.addAction(f"{entry['ns']}/{entry['name']} port {entry['remote']}", lambda e=entry: self._forget(e))

    def _forget(self, entry):
        try:
            self.saved.remove(entry)
        except OSError as e:
            QMessageBox.warning(self, "Klotse", str(e))

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        # the table can briefly be longer than the list (a forward was just cleared), so the row is checked
        return self.manager.items[rows[0].row()] if rows and rows[0].row() < len(self.manager.items) else None

    def refresh(self):
        selected = self._selected()
        self.table.setRowCount(len(self.manager.items))
        for r, fw in enumerate(self.manager.items):
            state = STATE_TEXT[fw.state] + (f": {fw.message}" if fw.state == "failed" and fw.message else "")
            values = [fw.target, fw.namespace, fw.address or "(waiting)", str(fw.remote), state, str(fw.connections)]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, c, item)
        if selected in self.manager.items:
            self.table.selectRow(self.manager.items.index(selected))
        self.table.resizeColumnsToContents()
        self._update_buttons()

    def _update_buttons(self, *_):
        fw = self._selected()
        self.stop_button.setEnabled(fw is not None and fw.running)
        self.copy_button.setEnabled(fw is not None and bool(fw.address))
        self.open_button.setEnabled(fw is not None and fw.state == "active")
        self.stop_all_button.setEnabled(bool(self.manager.active()))
        self.clear_button.setEnabled(any(not fw.running for fw in self.manager.items))

    def _copy(self):
        fw = self._selected()
        if fw is not None and fw.address:
            QGuiApplication.clipboard().setText(fw.address)

    def _open(self):
        fw = self._selected()
        if fw is not None and fw.state == "active":
            webbrowser.open(f"http://{fw.address}/")

    def stop(self):
        """Called when the tab is closed or the cluster is: stop every forward."""
        self.manager.stop_all()


class PortForwardDialog(QDialog):
    """Which ports: the remote one (offered from the pod's or service's own ports, or typed) and, optionally, the local one."""

    def __init__(self, kind, name, namespace, ports, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Port forward: {name}")
        self.ports = list(ports)
        self.remote = QComboBox()
        self.remote.setEditable(True)
        for label, number in self.ports:
            self.remote.addItem(label, number)
        self.remote.lineEdit().setPlaceholderText("port number")
        self.local = QLineEdit(placeholderText="empty = a free port, chosen for you")
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(theme.warning_style())
        self.note.hide()
        header = QLabel(f"Forward <b>{kind.lower()} {_plain(namespace)}/{_plain(name)}</b> to this computer (127.0.0.1 only).")
        header.setTextFormat(Qt.RichText)
        form = QFormLayout()
        form.addRow("Remote port", self.remote)
        form.addRow("Local port", self.local)
        self.remember = QCheckBox("Remember this forward (Port forwards tab > Saved forwards)")
        form.addRow(self.remember)
        start = QPushButton("Start")
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
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addLayout(buttons)

    def values(self):
        """(local, remote); 0 for a local port left empty. ValueError with a message for a bad entry."""
        text = self.remote.currentText().strip()
        index = self.remote.findText(text)
        remote = valid_port(self.remote.itemData(index) if index >= 0 else text)
        return valid_port(self.local.text(), allow_empty=True), remote

    def _accept(self):
        try:
            self.result_ports = self.values()
        except ValueError as e:
            self.note.setText(str(e))
            self.note.show()
            return
        self.accept()


def _plain(text):
    from html import escape
    return escape(str(text))
