"""Browse the files of a container (Pod > Browse files…, PersistentVolumeClaim > Browse files…) and pick a path for Copy files….
Looking only: the listing is `kubectl exec ... ls` (see k8s/remote_files.py)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..k8s import remote_files as rf
from . import theme

KIND_TEXT = {rf.DIR: "📁 folder", rf.LINK: "↪ link", rf.FILE: "file"}


class FileBrowserDialog(QDialog):
    """`lister(container, path, on_ok, on_error)` starts a listing and later calls `on_ok(entries, cut)` or `on_error(message)`
    (the workspace runs it on a worker; tests pass a synchronous one). In `choose` mode the dialog returns a path
    (`chosen`) for the caller's field; otherwise it offers Copy path and Download…."""

    download_requested = Signal(str, str)  # container, path

    def __init__(self, title, containers, start_path, lister, choose=False, container=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)
        self.lister, self.choose, self.chosen = lister, choose, None
        self.path, self._serial = "/", 0
        self._open = True
        self.finished.connect(self._closed)
        self.container = QComboBox()
        self.container.addItems(list(containers))
        if container:
            self.container.setCurrentText(container)
        self.container.setVisible(len(containers) > 1)
        self.address = QLineEdit(start_path or "/")
        self.address.setPlaceholderText("absolute path, Enter to open")
        self.up = QPushButton("Up")
        self.go = QPushButton("Open")
        self.refresh = QPushButton("Refresh")
        self.status = QLabel()
        self.status.setWordWrap(True)
        theme.muted(self.status)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Type"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        top = QHBoxLayout()
        top.addWidget(self.container)
        top.addWidget(self.up)
        top.addWidget(self.address, 1)
        top.addWidget(self.go)
        top.addWidget(self.refresh)
        buttons = QHBoxLayout()
        self.select_button = self.copy_button = self.download_button = None
        if choose:
            self.select_button = QPushButton("Use selected (or this folder)")
            self.select_button.setDefault(True)
            self.select_button.clicked.connect(self._choose)
            buttons.addWidget(self.select_button)
        else:
            self.copy_button = QPushButton("Copy path")
            self.download_button = QPushButton("Download…")
            self.copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.selected_path()))
            self.download_button.clicked.connect(lambda: self.download_requested.emit(self.container.currentText(), self.selected_path()))
            buttons.addWidget(self.copy_button)
            buttons.addWidget(self.download_button)
        buttons.addStretch(1)
        close = QPushButton("Cancel" if choose else "Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)
        self.up.clicked.connect(lambda: self.open_path(rf.parent(self.path)))
        self.go.clicked.connect(lambda: self.open_path(self.address.text()))
        self.address.returnPressed.connect(lambda: self.open_path(self.address.text()))
        self.refresh.clicked.connect(lambda: self.open_path(self.path))
        self.container.currentTextChanged.connect(lambda _: self.open_path(self.path))
        self.table.cellDoubleClicked.connect(lambda row, _c: self._enter(row))
        self.open_path(start_path or "/")

    # ---- navigation ----------------------------------------------------------------------------------------------
    def open_path(self, path):
        try:
            path = rf.clean_path(path)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._serial += 1
        serial = self._serial
        self.status.setText(f"Listing {path}…")
        self.lister(self.container.currentText(), path,
                    lambda entries, cut: self._guarded(self._listed, serial, path, entries, cut),
                    lambda message: self._guarded(self._failed, serial, message))

    def _guarded(self, fn, *args):
        """Run an answer handler unless the window has been closed or deleted meanwhile (a late answer from a worker)."""
        try:
            fn(*args)
        except RuntimeError:  # the Qt objects are already gone
            self._open = False

    def _closed(self, _result=0):
        self._open = False  # answers that arrive after the window is gone are dropped

    def _listed(self, serial, path, entries, cut):
        if not self._open or serial != self._serial:  # window closed, or an answer to an older request
            return
        self.path = path
        self.address.setText(path)
        self.table.setRowCount(len(entries))
        for row, (name, kind) in enumerate(entries):
            first = QTableWidgetItem(name + ("/" if kind == rf.DIR else ""))
            first.setData(Qt.UserRole, (name, kind))
            self.table.setItem(row, 0, first)
            self.table.setItem(row, 1, QTableWidgetItem(KIND_TEXT[kind]))
        self.table.resizeColumnToContents(0)
        note = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}" + (f" (only the first {rf.LIMIT} are shown)" if cut else "")
        self.status.setText(note + ". Double-click a folder to open it.")

    def _failed(self, serial, message):
        if self._open and serial == self._serial:
            self.status.setText(message)

    def _enter(self, row):
        name, kind = self.table.item(row, 0).data(Qt.UserRole)
        if kind in (rf.DIR, rf.LINK):  # a link may point at a folder; if it does not, the listing says so
            self.open_path(rf.join(self.path, name))

    # ---- picking -------------------------------------------------------------------------------------------------
    def selected_path(self):
        """The selected entry's full path, or the folder being shown when nothing is selected."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return self.path
        name, _ = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return rf.join(self.path, name)

    def _choose(self):
        self.chosen = (self.container.currentText(), self.selected_path())
        self.accept()
