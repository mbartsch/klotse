"""Form editor for the subjects of a RoleBinding or ClusterRoleBinding.

The role a binding refers to (`roleRef`) cannot be changed after the binding is created (Kubernetes RBAC docs), so it is
shown read-only; only `subjects` is edited and everything else in the manifest is kept. Service accounts are picked
from the cluster's own (searchable, at most RESULT_LIMIT shown); users and groups are typed, with the well-known
groups offered. Names that need care ask for confirmation before saving."""
from copy import deepcopy
from html import escape

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QToolButton, QToolTip, QVBoxLayout, QWidget)

from . import theme
from .edit_dialog import leave_for_yaml
from .role_editor import (AUTOUPDATE, DEFAULTS_LABEL, RESULT_LIMIT, RULE_KINDS, Picker, confirm_risky, role_warnings)
from .service_account_editor import service_account_warnings

BINDING_KINDS = ("RoleBinding", "ClusterRoleBinding")
RBAC_API_GROUP = "rbac.authorization.k8s.io"
USER, GROUP, ACCOUNT = "User", "Group", "ServiceAccount"
KINDS = (USER, GROUP, ACCOUNT)
WELL_KNOWN_GROUPS = (
    ("system:authenticated", "every authenticated user"),
    ("system:unauthenticated", "anyone who has not authenticated"),
    ("system:serviceaccounts", "every service account in the cluster"),
    ("system:masters", "superusers: authorization checks are skipped for this group"),
)
BINDING_HELP = (
    "<b>Subjects</b> are who gets the role."
    "<ul>"
    "<li><b>User</b> and <b>Group</b>: the names come from your authentication system; Kubernetes does not store "
    "them. There is no required format, but the <code>system:</code> prefix is reserved for Kubernetes.</li>"
    "<li><b>ServiceAccount</b>: needs a namespace and a name. Its user name is "
    "<code>system:serviceaccount:&lt;namespace&gt;:&lt;name&gt;</code>.</li>"
    "<li>Well-known groups: <code>system:authenticated</code>, <code>system:unauthenticated</code>, "
    "<code>system:serviceaccounts</code> (all) and <code>system:serviceaccounts:&lt;namespace&gt;</code>.</li>"
    "<li>The <b>role</b> a binding refers to cannot be changed after the binding is created. To use another role, "
    "delete the binding and create a new one.</li>"
    "</ul>")


# ---- pure helpers (tested without Qt) ---------------------------------------------
def normalize_subject(subject):
    """One subject as the API wants it: User and Group carry the RBAC apiGroup, a ServiceAccount a namespace and no
    apiGroup. Strings are trimmed."""
    kind = str((subject or {}).get("kind", "")).strip()
    out = {"kind": kind, "name": str((subject or {}).get("name", "")).strip()}
    if kind == ACCOUNT:
        out["namespace"] = str(subject.get("namespace") or "").strip()
    elif kind in (USER, GROUP):
        out["apiGroup"] = RBAC_API_GROUP
    return out


def subject_key(subject):
    s = normalize_subject(subject)
    return (s["kind"], s["name"], s.get("namespace", ""))


def unique_subjects(subjects):
    seen, out = set(), []
    for s in subjects:
        if subject_key(s) not in seen:
            seen.add(subject_key(s))
            out.append(normalize_subject(s))
    return out


def validate_subjects(subjects):
    """Problems the API server would reject, as readable lines (empty = fine)."""
    problems = []
    for n, subject in enumerate(subjects, 1):
        s = normalize_subject(subject)
        where = f"Subject {n}: "
        if s["kind"] not in KINDS:
            problems.append(where + f"the kind must be one of {', '.join(KINDS)}")
        if not s["name"]:
            problems.append(where + "enter a name")
        if s["kind"] == ACCOUNT and not s.get("namespace"):
            problems.append(where + "a service account needs a namespace")
    return problems


def subject_label(subject):
    s = normalize_subject(subject)
    return f"{s['kind']} {s.get('namespace') + '/' if s.get('namespace') else ''}{s['name']}"


def subject_notes(subjects):
    """Hints that are not errors."""
    notes = []
    for s in map(normalize_subject, subjects):
        if s["kind"] == USER and s["name"].startswith("system:serviceaccount:"):
            notes.append(f"{s['name']} is a service account's user name; choose kind ServiceAccount instead")
    return notes


def subject_warnings(subjects):
    """Why adding these subjects needs care, one sentence each (empty list = nothing special)."""
    out = []
    for s in map(normalize_subject, subjects):
        name, kind = s["name"], s["kind"]
        if (kind == GROUP and name == "system:unauthenticated") or (kind == USER and name == "system:anonymous"):
            out.append(f"{subject_label(s)}: this gives the role to anyone who can reach the API server, without "
                       "logging in.")
        elif kind == GROUP and name == "system:authenticated":
            out.append(f"{subject_label(s)}: this gives the role to every authenticated user.")
        elif kind == GROUP and name == "system:masters":
            out.append(f"{subject_label(s)}: members of this group are superusers; authorization is not even checked.")
        elif kind == GROUP and (name == "system:serviceaccounts" or name.startswith("system:serviceaccounts:")):
            scope = name.partition("system:serviceaccounts:")[2]
            out.append(f"{subject_label(s)}: this gives the role to every service account"
                       f"{' in namespace ' + scope if scope else ' in the cluster'}.")
    return out


def binding_warnings(raw):
    """Why changing or deleting this binding needs care (Kubernetes RBAC docs)."""
    meta = raw.get("metadata") or {}
    labels, annotations = meta.get("labels") or {}, meta.get("annotations") or {}
    ref = raw.get("roleRef") or {}
    out = []
    if labels.get(DEFAULTS_LABEL[0]) == DEFAULTS_LABEL[1] or str(meta.get("name", "")).startswith("system:"):
        reconcile = ("Automatic reconciliation is switched off for it (autoupdate=false), so a change stays."
                     if str(annotations.get(AUTOUPDATE, "")).lower() == "false" else
                     "At every API server start-up missing default subjects are added back, so removing subjects "
                     "does not last; wrong edits can still break the cluster's own components.")
        out.append("This is a default (system) binding. The system: prefix is reserved for Kubernetes. " + reconcile)
    if ref.get("name") == "cluster-admin":
        where = "in the whole cluster" if raw.get("kind") == "ClusterRoleBinding" or not meta.get("namespace") \
            else f"in namespace {meta['namespace']}"
        out.append(f"This binding grants cluster-admin: everyone it names can do anything {where}.")
    return out


def care_warnings(kind, raw):
    """Warnings for any RBAC object that can be edited or deleted (roles, bindings and service accounts)."""
    if kind in RULE_KINDS:
        return role_warnings(raw)
    if kind in BINDING_KINDS:
        return binding_warnings(raw)
    if kind == "ServiceAccount":
        return service_account_warnings(raw)
    return []


# ---- the dialog -------------------------------------------------------------------
class BindingEditDialog(QDialog):
    """Edit the subjects of a binding. After Save, `result_body` is the manifest with the new subjects; "Edit as
    YAML…" sets `wants_yaml` and rejects the dialog."""

    def __init__(self, kind, raw, directory, parent=None):
        super().__init__(parent)
        self.kind, self.directory, self._raw = kind, directory, raw
        self.result_body, self.wants_yaml = None, False
        meta = raw.get("metadata") or {}
        self.namespace = meta.get("namespace") or ""
        self.subjects = [normalize_subject(s) for s in raw.get("subjects") or []]
        self._original = {subject_key(s) for s in self.subjects}
        self._loading = False
        self.warnings = binding_warnings({**raw, "kind": kind})
        self.setWindowTitle(f"Edit {kind} {meta.get('name', '')}")
        self.resize(820, 640)

        ref = raw.get("roleRef") or {}
        where = f" in namespace <b>{escape(self.namespace)}</b>" if self.namespace else ""
        self.role_label = QLabel(f"Grants {escape(str(ref.get('kind', '')))} <b>{escape(str(ref.get('name', '')))}</b>"
                                 f"{where}. The role cannot be changed after a binding is created.")
        self.role_label.setWordWrap(True)
        self.help_button = QToolButton(text="?")
        self.help_button.setAutoRaise(True)
        self.help_button.setCursor(Qt.WhatsThisCursor)
        self.help_button.setToolTip(BINDING_HELP)
        self.help_button.clicked.connect(lambda: QToolTip.showText(QCursor.pos(), BINDING_HELP, self.help_button))
        head = QHBoxLayout()
        head.addWidget(self.role_label, 1)
        head.addWidget(self.help_button, 0, Qt.AlignTop)

        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(w) for w in self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(self.warnings))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Kind", "Name", "Namespace"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.remove_button = QPushButton("Remove selected")
        self.hint = QLabel()
        self.hint.setWordWrap(True)

        self.add_kind = QComboBox()
        self.add_kind.addItems(["User", "Group", "Service account"])
        self.name_edit = QLineEdit(placeholderText="User name as your authenticator reports it")
        self.name_completer = QCompleter([n for n, _d in WELL_KNOWN_GROUPS], self)
        self.name_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.name_completer.setFilterMode(Qt.MatchContains)
        self.add_name_button = QPushButton("Add")
        name_page = QWidget()
        name_row = QHBoxLayout(name_page)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(self.add_name_button)
        self.accounts = Picker("Service accounts", lambda q: self.directory.search(q, RESULT_LIMIT),
                               lambda e: (f"{e[0]}/{e[1]}", f"{e[0]}/{e[1]}", e), direct=True,
                               placeholder="Search service accounts (namespace/name)…")
        self.add_typed_button = QPushButton("Add typed namespace/name")
        self.add_typed_button.setToolTip("Bind a service account that does not exist yet, or that is not listed")
        self.accounts_status = QLabel()
        account_page = QWidget()
        account_col = QVBoxLayout(account_page)
        account_col.setContentsMargins(0, 0, 0, 0)
        account_col.addWidget(self.accounts)
        typed = QHBoxLayout()
        typed.addWidget(self.accounts_status, 1)
        typed.addWidget(self.add_typed_button)
        account_col.addLayout(typed)
        self.pages = QStackedWidget()
        self.pages.addWidget(name_page)
        self.pages.addWidget(account_page)
        add_box = QGroupBox("Add a subject")
        add_lay = QVBoxLayout(add_box)
        add_lay.addWidget(self.add_kind)
        add_lay.addWidget(self.pages)

        self.yaml_button = QPushButton("Edit as YAML…")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.yaml_button)
        bottom.addWidget(buttons)
        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(self.banner)
        lay.addWidget(QLabel("<b>Subjects</b> (double-click a name or namespace to edit it)"))
        lay.addWidget(self.table, 1)
        lay.addWidget(self.remove_button)
        lay.addWidget(self.hint)
        lay.addWidget(add_box)
        lay.addLayout(bottom)

        self.table.itemChanged.connect(self._cell_changed)
        self.remove_button.clicked.connect(self._remove_selected)
        self.add_kind.currentIndexChanged.connect(self._kind_page)
        self.add_name_button.clicked.connect(self._add_named)
        self.name_edit.returnPressed.connect(self._add_named)
        self.accounts.picked.connect(self._accounts_picked)
        self.add_typed_button.clicked.connect(self._add_typed)
        self.yaml_button.clicked.connect(self._as_yaml)
        self._refresh = QTimer(self, singleShot=True, interval=200)
        self._refresh.timeout.connect(self._directory_changed)
        directory.finished.connect(self._refresh.start)

        self._fill()
        self._kind_page()
        self._directory_changed()

    # ---- the directory of service accounts ----
    def _directory_changed(self):
        self.accounts_status.setText(self.directory.status_text())
        self.accounts.refresh()

    def done(self, code):
        try:
            self.directory.finished.disconnect(self._refresh.start)
        except (RuntimeError, TypeError):
            pass
        super().done(code)

    # ---- the table ----
    def _fill(self):
        self._loading = True
        try:
            self.table.setRowCount(len(self.subjects))
            for row, s in enumerate(self.subjects):
                combo = QComboBox()
                combo.addItems(KINDS)
                combo.setCurrentText(s["kind"] if s["kind"] in KINDS else USER)
                combo.currentTextChanged.connect(lambda text, row=row: self._kind_changed(row, text))
                self.table.setCellWidget(row, 0, combo)
                self.table.setItem(row, 1, QTableWidgetItem(s["name"]))
                self.table.setItem(row, 2, self._namespace_item(s))
        finally:
            self._loading = False
        self._hint()

    @staticmethod
    def _namespace_item(subject):
        item = QTableWidgetItem(subject.get("namespace", ""))
        if subject["kind"] != ACCOUNT:
            item.setFlags(Qt.NoItemFlags)  # only a service account has a namespace
        return item

    def _kind_changed(self, row, kind):
        if self._loading or not 0 <= row < len(self.subjects):
            return
        s = self.subjects[row]
        s["kind"] = kind
        if kind != ACCOUNT:
            s.pop("namespace", None)
        else:
            s.setdefault("namespace", self.namespace)
        self.subjects[row] = normalize_subject(s)
        self._loading = True  # update the namespace cell in place: rebuilding would delete the combo that is signalling
        self.table.setItem(row, 2, self._namespace_item(self.subjects[row]))
        self._loading = False
        self._hint()

    def _cell_changed(self, item):
        if self._loading or not 0 <= item.row() < len(self.subjects):
            return
        s = self.subjects[item.row()]
        if item.column() == 1:
            s["name"] = item.text().strip()
        elif item.column() == 2 and s["kind"] == ACCOUNT:
            s["namespace"] = item.text().strip()
        self._hint()

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.subjects):
                del self.subjects[row]
        if rows:
            self._fill()

    def _hint(self):
        text = "; ".join([p.split(": ", 1)[1] for p in validate_subjects(self.subjects)[:1]] +
                         subject_notes(self.subjects)[:1])
        self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(text)}</span>" if text else "")

    # ---- adding ----
    def _kind_page(self, *_):
        index = self.add_kind.currentIndex()
        self.pages.setCurrentIndex(1 if index == 2 else 0)
        self.name_edit.setPlaceholderText("Group name as your authenticator reports it" if index == 1
                                          else "User name as your authenticator reports it")
        self.name_edit.setCompleter(self.name_completer if index == 1 else None)

    def _add(self, subjects):
        keys = {subject_key(s) for s in self.subjects}
        fresh = [s for s in unique_subjects(subjects) if subject_key(s) not in keys]
        if fresh:
            self.subjects.extend(fresh)
            self._fill()
            self.table.scrollToBottom()
        return fresh

    def _add_named(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self._add([{"kind": GROUP if self.add_kind.currentIndex() == 1 else USER, "name": name}])
        self.name_edit.clear()

    def _accounts_picked(self, picks):
        self._add([{"kind": ACCOUNT, "namespace": ns, "name": name} for _value, (ns, name) in picks])

    def _add_typed(self):
        text = self.accounts.query.text().strip()
        namespace, slash, name = text.rpartition("/")
        if not slash:  # just a name: a RoleBinding's own namespace is the natural default
            namespace = self.namespace
        namespace, name = namespace.strip(), name.strip()
        if not namespace or not name:
            self.accounts_status.setText("Type namespace/name, for example kube-system/default")
            return
        self._add([{"kind": ACCOUNT, "namespace": namespace, "name": name}])
        self.accounts.query.clear()

    # ---- result ----
    def pending_body(self):
        """The manifest with the subjects as they are in the form now (not validated)."""
        body = deepcopy(self._raw)
        subjects = unique_subjects(self.subjects)
        if subjects:
            body["subjects"] = subjects
        else:
            body.pop("subjects", None)
        return body

    def _as_yaml(self):
        leave_for_yaml(self)

    def _save(self):
        problems = validate_subjects(self.subjects)
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        subjects = unique_subjects(self.subjects)
        added = [s for s in subjects if subject_key(s) not in self._original]  # only what is new needs a warning
        warnings = self.warnings + subject_warnings(added)
        name = (self._raw.get("metadata") or {}).get("name", "")
        if not confirm_risky(self, self.kind, name, warnings):
            return
        body = deepcopy(self._raw)
        if subjects:
            body["subjects"] = subjects
        else:
            body.pop("subjects", None)
        self.result_body = body
        self.accept()
