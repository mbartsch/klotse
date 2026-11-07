"""View > RBAC lookup…: "who can do X?" and "what can this user, group or service account do?", worked out from the cluster's Roles,
ClusterRoles and bindings (see k8s/rbac_lookup.py). Looking only."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..k8s.rbac_lookup import Rbac, rule_rows
from . import theme

VERBS = ("get", "list", "watch", "create", "update", "patch", "delete", "deletecollection", "*")
SUBJECT_KINDS = ("ServiceAccount", "User", "Group")
CAVEAT = ("According to the RBAC objects only (Roles, ClusterRoles and their bindings). Not included: other authorizers (webhooks, "
          "cloud IAM mapped to groups) and the built-in group system:masters, which is always allowed.")


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows):
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            item = QTableWidgetItem(str(text))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, j, item)
    table.resizeColumnsToContents()


class RbacDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RBAC lookup")
        self.resize(1000, 600)
        self.rbac = None
        self.header = QLabel(f"<b>{_plain(context)}</b>")
        self.header.setTextFormat(Qt.RichText)
        self.status = QLabel("Loading roles and bindings…")
        self.status.setWordWrap(True)
        theme.muted(self.status)

        # tab 1: who can?
        self.verb = QComboBox()
        self.verb.addItems(VERBS)
        self.resource = QLineEdit(placeholderText="pods, deployments, pods/log, secrets, * …")
        self.group = QLineEdit(placeholderText="apps, batch … (empty = core)")
        self.namespace = QLineEdit(placeholderText="empty = cluster-wide grants only")
        self.name = QLineEdit(placeholderText="optional: one object's name")
        self.ask = QPushButton("Who can?")
        self.ask.setDefault(True)
        self.ask.clicked.connect(self.run_who_can)
        self.resource.returnPressed.connect(self.run_who_can)
        form = QFormLayout()
        form.addRow("Verb", self.verb)
        form.addRow("Resource", self.resource)
        form.addRow("API group", self.group)
        form.addRow("Namespace", self.namespace)
        form.addRow("Object name", self.name)
        self.who_result = QLabel()
        self.who_result.setWordWrap(True)
        self.who_table = _table(["Kind", "Subject", "Namespace", "Granted", "Through", "Note"])
        who = QWidget()
        lay = QVBoxLayout(who)
        lay.addLayout(form)
        lay.addWidget(self.ask)
        lay.addWidget(self.who_result)
        lay.addWidget(self.who_table, 1)

        # tab 2: what can they do?
        self.subject_kind = QComboBox()
        self.subject_kind.addItems(SUBJECT_KINDS)
        self.subject_name = QLineEdit(placeholderText="name of the service account, user or group")
        self.subject_namespace = QLineEdit(placeholderText="service accounts only")
        self.look = QPushButton("What can it do?")
        self.look.clicked.connect(self.run_permissions)
        self.subject_name.returnPressed.connect(self.run_permissions)
        form2 = QFormLayout()
        form2.addRow("Kind", self.subject_kind)
        form2.addRow("Name", self.subject_name)
        form2.addRow("Namespace", self.subject_namespace)
        self.perm_result = QLabel()
        self.perm_result.setWordWrap(True)
        self.perm_table = _table(["Scope", "Through", "Given by group", "Verbs", "Resources / URLs", "API groups", "Names"])
        what = QWidget()
        lay2 = QVBoxLayout(what)
        lay2.addLayout(form2)
        lay2.addWidget(self.look)
        lay2.addWidget(self.perm_result)
        lay2.addWidget(self.perm_table, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(who, "Who can…?")
        self.tabs.addTab(what, "What can a subject do?")
        self.reload_button = QPushButton("Reload")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        caveat = QLabel(CAVEAT)
        caveat.setWordWrap(True)
        theme.muted(caveat)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reload_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        outer = QVBoxLayout(self)
        outer.addWidget(self.header)
        outer.addWidget(self.status)
        outer.addWidget(self.tabs, 1)
        outer.addWidget(caveat)
        outer.addLayout(buttons)
        self._set_ready(False)

    def _set_ready(self, ready):
        for button in (self.ask, self.look):
            button.setEnabled(ready)

    def show_data(self, data):
        """`data` is the dict `KubeService.rbac_data` returns."""
        self.rbac = Rbac(data.get("roles", []), data.get("clusterroles", []), data.get("rolebindings", []), data.get("clusterrolebindings", []))
        counts = (len(self.rbac.roles), len(self.rbac.clusterroles), len(self.rbac.rolebindings), len(self.rbac.clusterrolebindings))
        text = "{} Roles, {} ClusterRoles, {} RoleBindings, {} ClusterRoleBindings loaded.".format(*counts)
        missing = data.get("missing") or []
        self.status.setText(text + (f" Could not read: {', '.join(missing)} (answers are incomplete)." if missing else ""))
        self._set_ready(True)

    def show_error(self, message):
        self.status.setText(f"Could not load: {message}")

    def focus_subject(self, kind, name, namespace=""):
        """Open on the second tab for one subject (used by a service account's menu) and answer at once when the data is here."""
        self.tabs.setCurrentIndex(1)
        self.subject_kind.setCurrentText(kind)
        self.subject_name.setText(name)
        self.subject_namespace.setText(namespace)
        if self.rbac is not None:
            self.run_permissions()

    # ---- the two questions ---------------------------------------------------------------------------------------
    def run_who_can(self):
        if self.rbac is None:
            return
        resource = self.resource.text().strip()
        if not resource:
            self.who_result.setText("Type a resource, for example pods or pods/log (or * for everything).")
            return
        namespace = self.namespace.text().strip()
        entries = self.rbac.who_can(self.verb.currentText(), self.group.text().strip(), resource, namespace, self.name.text().strip())
        rows = []
        for e in entries:
            notes = []
            if e["wide"]:
                notes.append("everyone in this group")
            if e["limited"]:
                notes.append("only for named objects")
            rows.append((e["kind"], e["name"], e["namespace"], e["scope"], e["via"], "; ".join(notes)))
        _fill(self.who_table, rows)
        where = f"in namespace {namespace}" if namespace else "cluster-wide"
        self.who_result.setText(f"{len(entries)} subject{'s' if len(entries) != 1 else ''} can {self.verb.currentText()} {resource} {where}." if entries
                                else f"No subject can {self.verb.currentText()} {resource} {where} through RBAC.")

    def run_permissions(self):
        if self.rbac is None:
            return
        name = self.subject_name.text().strip()
        if not name:
            self.perm_result.setText("Type the name of the service account, user or group.")
            return
        kind = self.subject_kind.currentText()
        namespace = self.subject_namespace.text().strip()
        if kind == "ServiceAccount" and not namespace:
            self.perm_result.setText("A service account needs its namespace.")
            return
        entries = self.rbac.permissions_of(kind, name, namespace)
        rows = rule_rows(entries)
        _fill(self.perm_table, rows)
        self.perm_result.setText(f"{len(entries)} binding{'s' if len(entries) != 1 else ''} reach{'es' if len(entries) == 1 else ''} "
                                 f"{kind} {name}, giving {len(rows)} rule{'s' if len(rows) != 1 else ''}." if entries
                                 else f"No binding reaches {kind} {name}: it has no permissions through RBAC.")


def _plain(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
