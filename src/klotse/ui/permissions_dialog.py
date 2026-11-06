"""View > My permissions…: what the signed-in user may do in a namespace (SelfSubjectRulesReview) and a "Can I?" check
for one verb on one resource (SelfSubjectAccessReview). Both are the user's own permissions, read from the API server."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from . import theme

VERBS = ("get", "list", "watch", "create", "update", "patch", "delete", "deletecollection")


def rule_rows(perms):
    """Table rows (kind, what, group, verbs, names) from the dict `KubeService.my_permissions` returns; resource rules
    first, then URLs. `*` is kept as it is: it means everything."""
    rows = []
    for rule in perms.get("resources") or []:
        groups = ", ".join(g if g else "(core)" for g in rule.get("groups") or []) or "(core)"
        rows.append(("resource", ", ".join(rule.get("resources") or []), groups, ", ".join(rule.get("verbs") or []),
                     ", ".join(rule.get("names") or [])))
    for rule in perms.get("urls") or []:
        rows.append(("url", ", ".join(rule.get("urls") or []), "", ", ".join(rule.get("verbs") or []), ""))
    return rows


def verdict(allowed, reason=""):
    """'✔ Allowed' / '✖ Denied' with the server's reason: the answer never depends on colour alone."""
    text = "✔ Allowed" if allowed else "✖ Denied"
    return f"{text} — {reason}" if reason else text


class PermissionsDialog(QDialog):
    check_requested = Signal(str, str, str, str)  # verb, API group, resource (may be `pods/log`), resource name

    def __init__(self, context, namespace, perms, parent=None):
        super().__init__(parent)
        self.setWindowTitle("My permissions")
        self.resize(760, 520)
        self.header = QLabel(f"<b>{_plain(context)}</b> · namespace <b>{_plain(namespace)}</b>")
        self.header.setTextFormat(Qt.RichText)
        self.note = QLabel()
        self.note.setWordWrap(True)
        theme.muted(self.note)
        self.filter = QLineEdit(placeholderText="Filter by resource, group, verb or name…", clearButtonEnabled=True)
        self.filter.textChanged.connect(self._filter)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Kind", "Resources / URLs", "API group", "Verbs", "Names"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(True)
        self.verb = QComboBox()
        self.verb.addItems(VERBS)
        self.resource = QLineEdit(placeholderText="pods, deployments, pods/log, secrets…")
        self.group = QLineEdit(placeholderText="apps, batch, rbac.authorization.k8s.io… (empty = core)")
        self.name = QLineEdit(placeholderText="optional: one object's name")
        self.check_button = QPushButton("Check")
        self.check_button.setDefault(True)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.resource.returnPressed.connect(self._check)
        self.check_button.clicked.connect(self._check)
        form = QFormLayout()
        form.addRow("Can I", self.verb)
        form.addRow("resource", self.resource)
        form.addRow("API group", self.group)
        form.addRow("named", self.name)
        buttons = QHBoxLayout()
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.result, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.header)
        layout.addWidget(self.note)
        layout.addWidget(self.filter)
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("<b>Can I…?</b> Ask the API server about one thing:"))
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(close, 0, Qt.AlignRight)
        self._fill(perms)

    def _fill(self, perms):
        rows = rule_rows(perms)
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        notes = []
        if perms.get("incomplete"):
            notes.append("The server says this list may be incomplete (some authorizers cannot list their rules): use the "
                         "check below for a definite answer.")
        if perms.get("error"):
            notes.append(f"Evaluation error: {perms['error']}")
        if not rows:
            notes.append("The server returned no rules for this namespace.")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))

    def _filter(self, text):
        words = text.lower().split()
        for r in range(self.table.rowCount()):
            haystack = " ".join(self.table.item(r, c).text().lower() for c in range(self.table.columnCount()))
            self.table.setRowHidden(r, not all(w in haystack for w in words))

    def _check(self):
        resource = self.resource.text().strip()
        if not resource:
            self.result.setText("Type a resource first, for example pods.")
            return
        self.result.setText("Asking the API server…")
        self.check_requested.emit(self.verb.currentText(), self.group.text().strip(), resource, self.name.text().strip())

    def show_check(self, allowed=None, reason="", error=None):
        """The answer to the last check (or why there is none)."""
        self.result.setText(f"Cannot check: {error}" if error else verdict(allowed, reason))


def _plain(text):
    from html import escape
    return escape(str(text))
