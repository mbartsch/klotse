"""A Helm release in detail: what it is (chart, versions, status, notes), its history and its values. Values stay hidden until the
user presses Show values, and values that look secret (passwords, tokens, keys, certificates) stay `<hidden>` until a second
box is ticked. The dialog only shows and asks; the workspace confirms and runs the uninstall."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..k8s import helm
from . import theme
from .manifest import dump_manifest
from .rollout_dialog import created_text

HISTORY_HEADERS = ["Revision", "Status", "Updated", "Chart", "App version", "Description"]
HIDDEN_NOTE = "Values are hidden until you press Show values. They can contain passwords and tokens, so they are never logged."


class HelmReleaseDialog(QDialog):
    uninstall_requested = Signal()

    def __init__(self, context, data, can_change=True, parent=None):
        super().__init__(parent)
        self.data = data
        summary = data["summary"]
        self.setWindowTitle(f"Helm release: {summary['name']}")
        self.resize(820, 560)
        self.header = QLabel(f"<b>{_plain(summary['name'])}</b> in <b>{_plain(summary['namespace'])}</b> on <b>{_plain(context)}</b>")
        self.header.setTextFormat(Qt.RichText)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._summary_tab(summary), "Summary")
        self.tabs.addTab(self._values_tab(), "Values")
        self.tabs.addTab(self._history_tab(data["history"]), "History")
        self.uninstall_button = QPushButton("Uninstall…")
        self.uninstall_button.setEnabled(can_change)
        self.uninstall_button.setToolTip("Runs `helm uninstall` (the helm command must be installed)" if can_change
                                         else "This cluster is read-only")
        self.uninstall_button.clicked.connect(self.uninstall_requested.emit)
        self.status = QLabel()
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.uninstall_button)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        if data.get("skipped"):
            note = QLabel(f"{data['skipped']} revision(s) could not be read and are left out.")
            note.setStyleSheet(theme.warning_style())
            layout.addWidget(note)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons)

    # ---- tabs ----------------------------------------------------------------------------------------------------
    def _summary_tab(self, s):
        page = QWidget()
        facts = [("Status", s["status"]), ("Revision", s["revision"]), ("Chart", f"{s['chart']} {s['chart_version']}".strip()),
                 ("App version", s["app_version"]), ("Description", s["description"]),
                 ("First deployed", s["first_deployed"]), ("Last deployed", s["last_deployed"])]
        self.facts = QLabel("\n".join(f"{k}: {v}" for k, v in facts if v not in ("", None)))
        self.facts.setTextFormat(Qt.PlainText)
        self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notes = QPlainTextEdit(s["notes"] or "")
        self.notes.setReadOnly(True)
        self.notes.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.notes.setPlaceholderText("This release has no notes.")
        layout = QVBoxLayout(page)
        layout.addWidget(self.facts)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self.notes, 1)
        return page

    def _values_tab(self):
        page = QWidget()
        self.which = QComboBox()
        self.which.addItems(["User-supplied values", "All values (chart defaults and yours)"])
        self.reveal = QCheckBox("Reveal values that look secret")
        self.show_button = QPushButton("Show values")
        self.values = QPlainTextEdit()
        self.values.setReadOnly(True)
        self.values.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.values.setPlaceholderText(HIDDEN_NOTE)
        self._shown = False
        self.show_button.clicked.connect(self._toggle_values)
        self.which.currentIndexChanged.connect(self._render_values)
        self.reveal.toggled.connect(self._render_values)
        row = QHBoxLayout()
        row.addWidget(self.which)
        row.addWidget(self.reveal)
        row.addWidget(self.show_button)
        row.addStretch(1)
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self.values, 1)
        return page

    def _history_tab(self, history):
        self.history = QTableWidget(len(history), len(HISTORY_HEADERS))
        self.history.setHorizontalHeaderLabels(HISTORY_HEADERS)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history.setSelectionBehavior(QTableWidget.SelectRows)
        for r, rev in enumerate(history):
            values = [str(rev["revision"]), rev["status"], created_text(rev["last_deployed"]),
                      f"{rev['chart']} {rev['chart_version']}".strip(), rev["app_version"], rev["description"]]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.history.setItem(r, c, item)
        self.history.resizeColumnsToContents()
        return self.history

    # ---- values --------------------------------------------------------------------------------------------------
    def _toggle_values(self):
        self._shown = not self._shown
        self.show_button.setText("Hide values" if self._shown else "Show values")
        self._render_values()

    def _render_values(self, *_):
        """The chosen values as YAML while shown (secret-looking ones masked unless revealed); nothing otherwise."""
        if not self._shown:
            self.values.clear()
            return
        values = self.data["values_user"] if self.which.currentIndex() == 0 else self.data["values_all"]
        shown = values if self.reveal.isChecked() else helm.mask_values(values)
        self.values.setPlainText(dump_manifest(shown) if shown else "# no values\n")

    def show_status(self, text):
        self.status.setText(text)


def _plain(text):
    from html import escape
    return escape(str(text))
