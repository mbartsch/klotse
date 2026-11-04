"""Form editor for a ServiceAccount: token automount, secrets, image pull secrets, annotations and labels.

Secrets are picked from the names in the account's namespace (names only: no secret data is fetched) or typed; annotations
and labels are key/value tables, with suggestions for the annotations that cloud identity integrations use. Only these
fields change: the rest of the manifest is kept, and the very long `last-applied-configuration` annotation is hidden
from the table but preserved."""
from copy import deepcopy
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QToolButton, QToolTip, QVBoxLayout, QWidget)

from .role_editor import AUTOUPDATE, RESULT_LIMIT, Picker, confirm_risky

LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
ANNOTATION_SUGGESTIONS = (
    "eks.amazonaws.com/role-arn",  # AWS (EKS): IAM role for this service account
    "eks.amazonaws.com/sts-regional-endpoints",
    "iam.gke.io/gcp-service-account",  # Google (GKE) Workload Identity
    "azure.workload.identity/client-id",  # Azure Workload Identity
)
AUTOMOUNT_CHOICES = (
    ("Not set (the default is: mount the token)", None),
    ("Yes: pods get the token mounted", True),
    ("No: pods do not get the token mounted", False),
)
AUTOMOUNT_TIP = "A pod's own automountServiceAccountToken setting wins over this one."
SECRETS_HELP = (
    "<b>Secrets</b> listed here are the ones the account may use. Token secrets that older Kubernetes versions "
    "created for every account are listed here; newer versions get tokens from the TokenRequest API instead, so "
    "this list is often empty.")
PULL_SECRETS_HELP = (
    "<b>Image pull secrets</b> (<code>kubernetes.io/dockerconfigjson</code> secrets) are added to every pod that "
    "uses this service account, so its images can be pulled from a private registry.")


# ---- pure helpers (tested without Qt) ---------------------------------------------
def validate_pairs(pairs, what):
    """Problems with (key, value) rows: empty or duplicate keys, spaces or '=' in a key. `what` = 'annotation'/'label'."""
    problems, seen = [], set()
    for n, (key, _value) in enumerate(pairs, 1):
        key = key.strip()
        if not key:
            problems.append(f"{what.capitalize()} {n}: enter a key")
        elif any(c.isspace() for c in key) or "=" in key:
            problems.append(f"{what.capitalize()} {n}: the key must not contain spaces or '='")
        elif key in seen:
            problems.append(f"{what.capitalize()} {n}: the key {key} is listed twice")
        seen.add(key)
    return problems


def service_account_warnings(raw):
    """Why changing or deleting this service account needs care."""
    meta = raw.get("metadata") or {}
    name, namespace = meta.get("name", ""), meta.get("namespace", "")
    out = []
    if name == "default":
        out.append(f"This is the default service account of namespace {namespace or '(unknown)'}: every pod that does "
                   "not name a service account uses it, so a change reaches all of them. Kubernetes recreates it if "
                   "it is deleted.")
    if namespace == "kube-system":
        out.append("Service accounts in kube-system usually belong to the cluster's own components and add-ons.")
    return out


def build_body(raw, automount, secrets, pull_secrets, labels, annotations):
    """The manifest with the edited fields; everything else is copied. Empty lists and maps remove their field.
    Secret entries that were already there keep all their fields (kind, uid, ...)."""
    body = deepcopy(raw)
    if automount is None:
        body.pop("automountServiceAccountToken", None)
    else:
        body["automountServiceAccountToken"] = bool(automount)
    old = {s.get("name"): s for s in raw.get("secrets") or [] if isinstance(s, dict)}
    for field, names, previous in (("secrets", secrets, old), ("imagePullSecrets", pull_secrets, {})):
        entries = [deepcopy(previous[n]) if n in previous else {"name": n} for n in names]
        if entries:
            body[field] = entries
        else:
            body.pop(field, None)
    meta = body.setdefault("metadata", {})
    for field, mapping in (("labels", labels), ("annotations", annotations)):
        if mapping:
            meta[field] = dict(mapping)
        else:
            meta.pop(field, None)
    return body


# ---- widgets ----------------------------------------------------------------------
class KeyValueEditor(QWidget):
    """An editable table of key/value pairs plus a row for adding one (the key box suggests known keys)."""

    changed = Signal()

    def __init__(self, title, suggestions=(), hidden_keys=(), height=110):
        super().__init__()
        self.hidden_keys = set(hidden_keys)
        self._hidden = {}  # values of hidden keys, kept and written back unchanged
        self._loading = False
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Key", "Value"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setFixedHeight(height)
        self.key_box = QComboBox()
        self.key_box.setEditable(True)
        self.key_box.addItems(list(suggestions))
        self.key_box.setCurrentIndex(-1)
        self.key_box.lineEdit().setPlaceholderText("Key")
        self.value_edit = QLineEdit(placeholderText="Value")
        self.add_button = QPushButton("Add / update")
        self.remove_button = QPushButton("Remove selected")
        add_row = QHBoxLayout()
        add_row.addWidget(self.key_box, 1)
        add_row.addWidget(self.value_edit, 1)
        add_row.addWidget(self.add_button)
        add_row.addWidget(self.remove_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        lay.addWidget(self.table)
        lay.addLayout(add_row)
        self.table.itemChanged.connect(lambda _i: None if self._loading else self.changed.emit())
        self.add_button.clicked.connect(self._add)
        self.value_edit.returnPressed.connect(self._add)
        self.remove_button.clicked.connect(self._remove)

    def set_items(self, mapping):
        self._hidden = {k: v for k, v in mapping.items() if k in self.hidden_keys}
        self._loading = True
        try:
            shown = [(k, v) for k, v in mapping.items() if k not in self.hidden_keys]
            self.table.setRowCount(len(shown))
            for row, (key, value) in enumerate(shown):
                self.table.setItem(row, 0, QTableWidgetItem(str(key)))
                self.table.setItem(row, 1, QTableWidgetItem(str(value)))
        finally:
            self._loading = False

    def pairs(self):
        rows = []
        for r in range(self.table.rowCount()):
            key, value = self.table.item(r, 0), self.table.item(r, 1)
            rows.append((key.text() if key else "", value.text() if value else ""))
        return rows

    def items(self):
        """The mapping to save: the table rows (keys trimmed) plus the hidden keys that were there."""
        out = {k.strip(): v for k, v in self.pairs() if k.strip()}
        out.update(self._hidden)
        return out

    def _add(self):
        key, value = self.key_box.currentText().strip(), self.value_edit.text()
        if not key:
            return
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text().strip() == key:
                self.table.item(r, 1).setText(value)  # an existing key is updated, not repeated
                break
        else:
            self._loading = True
            row = self.table.rowCount()
            self.table.setRowCount(row + 1)
            self.table.setItem(row, 0, QTableWidgetItem(key))
            self.table.setItem(row, 1, QTableWidgetItem(value))
            self._loading = False
            self.changed.emit()
        self.key_box.setCurrentIndex(-1)
        self.key_box.clearEditText()
        self.value_edit.clear()

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.changed.emit()


class ServiceAccountEditDialog(QDialog):
    """Edit a ServiceAccount. After Save, `result_body` is the new manifest; "Edit as YAML…" sets `wants_yaml` and
    rejects the dialog."""

    def __init__(self, kind, raw, directory, parent=None):
        super().__init__(parent)
        self.kind, self.directory, self._raw = kind, directory, raw
        self.result_body, self.wants_yaml = None, False
        meta = raw.get("metadata") or {}
        self.warnings = service_account_warnings(raw)
        self.setWindowTitle(f"Edit {kind} {meta.get('name', '')}")
        self.resize(780, 720)

        self.head = head = QLabel(f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                      f"<b>{escape(str(meta.get('namespace', '')))}</b>. Only the fields below change; "
                      "the rest of the manifest is kept.")
        head.setWordWrap(True)
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(w) for w in self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#3a2c0f; color:#f5a524; padding:6px; border-radius:4px;")
        self.banner.setVisible(bool(self.warnings))

        self.automount = QComboBox()
        for text, _value in AUTOMOUNT_CHOICES:
            self.automount.addItem(text)
        self.automount.setToolTip(AUTOMOUNT_TIP)
        current = raw.get("automountServiceAccountToken")
        self.automount.setCurrentIndex(next(i for i, (_t, v) in enumerate(AUTOMOUNT_CHOICES) if v is current))
        mount_row = QHBoxLayout()
        mount_row.addWidget(QLabel("<b>Mount the API token in pods</b>"))
        mount_row.addWidget(self.automount, 1)

        self.secrets = Picker("Secrets", self._search, lambda n: (n, n, None), help=SECRETS_HELP,
                              placeholder="Search the secrets of this namespace…")
        self.pull_secrets = Picker("Image pull secrets", self._search, lambda n: (n, n, None), help=PULL_SECRETS_HELP,
                                   placeholder="Search the secrets of this namespace…")
        self.secrets.set_values([s.get("name", "") for s in raw.get("secrets") or [] if isinstance(s, dict)])
        self.pull_secrets.set_values([s.get("name", "") for s in raw.get("imagePullSecrets") or []
                                      if isinstance(s, dict)])
        self.secrets_typed = QPushButton("Add typed name")
        self.pull_typed = QPushButton("Add typed name")
        for picker, button in ((self.secrets, self.secrets_typed), (self.pull_secrets, self.pull_typed)):
            button.setToolTip("Use a name that is not listed (the secret may not exist yet)")
            button.clicked.connect(lambda _=False, p=picker: self._add_typed(p))
            picker.layout().itemAt(1).layout().addWidget(button)  # next to the search box
        self.directory_status = QLabel()
        self.directory_status.setWordWrap(True)

        self.annotations = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,))
        self.labels = KeyValueEditor("Labels")
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))
        self.hint = QLabel()
        self.hint.setWordWrap(True)

        form = QWidget()
        form_lay = QVBoxLayout(form)
        form_lay.addLayout(mount_row)
        form_lay.addWidget(self.secrets)
        form_lay.addWidget(self.pull_secrets)
        form_lay.addWidget(self.directory_status)
        form_lay.addWidget(self.annotations)
        form_lay.addWidget(self.labels)
        form_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)

        self.yaml_button = QPushButton("Edit as YAML…")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.yaml_button)
        bottom.addWidget(buttons)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.banner)
        lay.addWidget(scroll, 1)
        lay.addLayout(bottom)

        self.annotations.changed.connect(self._hint)
        self.labels.changed.connect(self._hint)
        self.yaml_button.clicked.connect(self._as_yaml)
        self._refresh = QTimer(self, singleShot=True, interval=200)
        self._refresh.timeout.connect(self._directory_changed)
        directory.finished.connect(self._refresh.start)
        self._directory_changed()
        self._hint()

    # ---- the secret names ----
    def _search(self, query):
        return self.directory.search(query, RESULT_LIMIT)

    def _directory_changed(self):
        self.directory_status.setText(self.directory.status_text())
        self.secrets.refresh()
        self.pull_secrets.refresh()

    def _add_typed(self, picker):
        name = picker.query.text().strip()
        if name and picker.add_values([name]):
            picker.query.clear()

    def done(self, code):
        try:
            self.directory.finished.disconnect(self._refresh.start)
        except (RuntimeError, TypeError):
            pass
        super().done(code)

    # ---- result ----
    def _problems(self):
        return (validate_pairs(self.annotations.pairs(), "annotation") + validate_pairs(self.labels.pairs(), "label"))

    def _hint(self):
        problems = self._problems()
        self.hint.setText(f"<span style='color:#f5a524'>{escape(problems[0])}</span>" if problems else "")

    def _as_yaml(self):
        self.wants_yaml = True
        self.reject()

    def _save(self):
        problems = self._problems()
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        name = (self._raw.get("metadata") or {}).get("name", "")
        if not confirm_risky(self, self.kind, name, self.warnings):
            return
        automount = AUTOMOUNT_CHOICES[self.automount.currentIndex()][1]
        self.result_body = build_body(self._raw, automount, self.secrets.values(), self.pull_secrets.values(),
                                      self.labels.items(), self.annotations.items())
        self.accept()
