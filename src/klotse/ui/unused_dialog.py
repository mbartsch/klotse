"""View > Unused objects…: ConfigMaps, Secrets and PersistentVolumeClaims that nothing Klotse reads refers to (see k8s/unused.py).
Looking only: nothing is deleted from here. Double-click goes to the object."""
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..k8s import unused
from ..k8s.util import format_age
from . import theme

HEADERS = ["Kind", "Namespace", "Name", "Age", "Note"]
LABELS = (("ConfigMap", "ConfigMaps"), ("Secret", "Secrets"), ("PersistentVolumeClaim", "Claims"))
CAVEAT = ("Unused means no pod, workload template, Ingress or Service Account names the object. It is a lead, not a verdict: operators and "
          "controllers that find objects by label or by convention (cert-manager, Prometheus, Gateway API certificate references, custom "
          "resources, scripts) are not seen. Secrets are read as names and labels only. Nothing is deleted from here.")


def age_of(created):
    try:
        return format_age(datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(timezone.utc))
    except ValueError:
        return ""


class UnusedDialog(QDialog):
    go_to_requested = Signal(str, str, str)  # kind, namespace, name

    def __init__(self, context, scope, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unused objects")
        self.resize(900, 520)
        self.rows = []
        self.header = QLabel(f"<b>{_plain(context)}</b> · {_plain(scope)}")
        self.header.setTextFormat(Qt.RichText)
        self.summary = QLabel("Looking…")
        self.summary.setWordWrap(True)
        theme.muted(self.summary)
        self.kind_boxes = {}
        options = QHBoxLayout()
        for kind, label in LABELS:
            box = self.kind_boxes[kind] = QCheckBox(label)
            box.setChecked(True)
            box.toggled.connect(lambda _=None: self._apply_filter())
            options.addWidget(box)
        self.filter = QLineEdit(placeholderText="Filter by namespace, name or note…", clearButtonEnabled=True)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        options.addWidget(self.filter, 1)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.reload_button = QPushButton("Look again")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        note = QLabel(CAVEAT)
        note.setWordWrap(True)
        theme.muted(note)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addWidget(self.summary)
        lay.addLayout(options)
        lay.addWidget(self.table, 1)
        lay.addWidget(note)
        lay.addLayout(buttons)

    def show_result(self, rows, judged, problems=()):
        self.rows = list(rows)
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            for column, text in enumerate((row["kind"], row["namespace"], row["name"], age_of(row["created"]), row["note"])):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        text = unused.summary(self.rows, judged)
        if problems:
            text += " Could not read: " + "; ".join(problems) + "."
        self.summary.setText(text)
        self._apply_filter()

    def show_refused(self, missing, problems):
        """A list of what refers to objects could not be read: judging would call used objects unused, so no list is shown."""
        self.rows = []
        self.table.setRowCount(0)
        self.summary.setText("Cannot tell what is unused: " + ", ".join(missing) + " could not be read (" + "; ".join(problems) + "). "
                             "Without them used objects would look unused, so no list is shown.")

    def show_error(self, message):
        self.summary.setText(f"Could not look: {message}")

    def _apply_filter(self):
        words = self.filter.text().lower().split()
        for index, row in enumerate(self.rows):
            line = " ".join((row["namespace"], row["name"], row["note"])).lower()
            self.table.setRowHidden(index, not self.kind_boxes[row["kind"]].isChecked() or not all(w in line for w in words))

    def _open_row(self, index, _column):
        row = self.rows[index]
        self.go_to_requested.emit(row["kind"], row["namespace"], row["name"])


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
