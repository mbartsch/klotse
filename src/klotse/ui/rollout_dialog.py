"""A Deployment's revision history with a way back (`kubectl rollout history` / `rollout undo`). The dialog only shows and asks;
the workspace confirms the rollback and talks to the cluster."""
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout

from ..k8s.util import format_age
from . import theme

HEADERS = ["Revision", "State", "Created", "Images", "Replicas", "Change cause"]


def created_text(stamp):
    """'3h' for an RFC 3339 creation time (the age, like the lists show); the text itself when it cannot be read."""
    try:
        return format_age(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc))
    except ValueError:
        return str(stamp or "")


class RolloutDialog(QDialog):
    rollback_requested = Signal(dict)  # the revision (a row of k8s.rollout.history) to go back to

    def __init__(self, context, name, revisions, can_change=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Rollout history: {name}")
        self.resize(820, 420)
        self.can_change = can_change
        self.header = QLabel(f"<b>{_plain(name)}</b> on <b>{_plain(context)}</b>")
        self.header.setTextFormat(Qt.RichText)
        self.note = QLabel()
        self.note.setWordWrap(True)
        theme.muted(self.note)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_button)
        self.rollback_button = QPushButton("Roll back to this revision…")
        self.rollback_button.clicked.connect(self._rollback)
        self.status = QLabel()
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.rollback_button)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.note)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self.set_revisions(revisions)

    def set_revisions(self, revisions):
        self.revisions = list(revisions or [])
        self.table.setRowCount(len(self.revisions))
        for r, rev in enumerate(self.revisions):
            values = [str(rev["revision"]), "✔ current" if rev["current"] else "", created_text(rev["created"]),
                      ", ".join(rev["images"]), str(rev["replicas"]), rev["change_cause"]]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.note.setText("Kubernetes keeps the last few revisions (spec.revisionHistoryLimit); older ones are gone." if self.revisions
                          else "No revisions found. A Deployment that was never rolled out, or whose old ReplicaSets were cleaned up, has none.")
        if not self.can_change:
            self.note.setText(self.note.text() + " This cluster is read-only, so rolling back is off.")
        self._update_button()

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.revisions[rows[0].row()] if rows else None

    def _update_button(self, *_):
        rev = self.selected()
        self.rollback_button.setEnabled(bool(self.can_change and rev is not None and not rev["current"]))
        self.rollback_button.setToolTip("" if rev is None or not rev["current"] else "This is the revision the Deployment is on now")

    def _rollback(self):
        rev = self.selected()
        if rev is not None and not rev["current"] and self.can_change:
            self.rollback_requested.emit(rev)

    def show_status(self, text):
        self.status.setText(text)

    def show_error(self, message):
        self.status.setText(f"✖ {message}")


def _plain(text):
    from html import escape
    return escape(str(text))
