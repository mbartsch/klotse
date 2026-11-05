"""Form editor for a ConfigMap or a Secret: the keys on the left, the value of the chosen key on the right.

A **ConfigMap**'s text values are shown and edited as they are (multi-line files are fine); its binary values are listed
and kept untouched.

A **Secret** never shows a value until the user presses *Show value* for that one key, and only that key is decoded:
the other keys stay base64 text in memory and are written back exactly as they were. Saving an existing Secret sends a
*patch* that holds only the keys that were added, changed or removed (and changed labels or annotations), so untouched
values are not even sent back, and the Secret's masked `last-applied-configuration` annotation is never written. There is
deliberately no "Edit as YAML…" for a Secret: the YAML would hold the masked placeholder, not the values. A new Secret
is created with the values typed here.

The pure helpers (key rules, base64 handling, the patch) are tested without Qt."""
import base64
import binascii
import re
from copy import deepcopy
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .edit_dialog import leave_for_yaml
from .service_account_editor import ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor, validate_pairs

DATA_KINDS = ("ConfigMap", "Secret")
KEY_RULE = re.compile(r"[-._a-zA-Z0-9]+")
NAME_RULE = re.compile(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?")
SIZE_LIMIT = 1024 * 1024  # a ConfigMap or Secret may hold at most 1 MiB
HIDDEN = "<hidden>"
SECRET_TYPES = {
    "Opaque": "Any keys you like.",
    "kubernetes.io/tls": "Needs the keys tls.crt and tls.key (certificate and private key, PEM).",
    "kubernetes.io/dockerconfigjson": "Needs the key .dockerconfigjson (registry credentials as JSON), for image pull secrets.",
    "kubernetes.io/basic-auth": "Needs username and/or password.",
    "kubernetes.io/ssh-auth": "Needs the key ssh-privatekey.",
}
SHOW_NOTE = ("Values stay hidden until you press <b>Show value</b> for a key; only that key is decoded. Keys you do not "
             "touch are not changed, and saving sends only what you added, changed or removed.")


# ---- pure helpers -------------------------------------------------------------------
def new_manifest(kind, namespace):
    """What New ConfigMap / New Secret starts from."""
    return {"apiVersion": "v1", "kind": kind, "metadata": {"name": "", "namespace": namespace}}


def valid_key(key):
    return bool(KEY_RULE.fullmatch(key)) and key not in (".", "..") and len(key) <= 253


def decode_text(encoded):
    """The text of a base64 value, or None when it is not valid base64 or not UTF-8 text (binary)."""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def encode_text(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decoded_size(encoded):
    return len(encoded) * 3 // 4 - encoded[-2:].count("=")


def secret_patch(old_data, new_data, old_meta, new_meta):
    """A merge patch for a Secret: only the keys added or changed (with their base64 values), removed keys as None, and the
    labels and annotations that changed. {} when nothing changed. The masked last-applied annotation is never included."""
    patch = {}
    data = {k: v for k, v in new_data.items() if old_data.get(k) != v}
    data.update({k: None for k in old_data if k not in new_data})
    if data:
        patch["data"] = data
    meta = {}
    for field in ("labels", "annotations"):
        before, after = dict(old_meta.get(field) or {}), dict(new_meta.get(field) or {})
        before.pop(LAST_APPLIED, None)
        after.pop(LAST_APPLIED, None)
        diff = {k: v for k, v in after.items() if before.get(k) != v}
        diff.update({k: None for k in before if k not in after})
        if diff:
            meta[field] = diff
    if meta:
        patch["metadata"] = meta
    return patch


# ---- the key list and value pane --------------------------------------------------------
class DataEditor(QWidget):
    """Keys of a ConfigMap or Secret. `data` maps key -> stored value (plain text for a ConfigMap, base64 for a Secret);
    `binary` is a ConfigMap's binaryData (kept as it is). Nothing is decoded until a Secret key is shown."""

    changed = Signal()

    def __init__(self, data, secret, binary=None, read_only=False):
        super().__init__()
        self.secret, self.read_only = secret, read_only
        self.entries = []  # dicts: key, stored, text, shown_text, revealed, binary, new
        for key, stored in data.items():
            self.entries.append({"key": key, "stored": stored, "text": None if secret else stored, "shown_text": None,
                                 "revealed": not secret, "binary": False, "new": False})
        for key, stored in (binary or {}).items():
            self.entries.append({"key": key, "stored": stored, "text": None, "shown_text": None, "revealed": False,
                                 "binary": True, "new": False})
        self._loading = False
        self.keys = QListWidget()
        self.keys.setMaximumWidth(260)
        self.new_key = QLineEdit(placeholderText="New key")
        self.add_button = QPushButton("Add")
        self.remove_button = QPushButton("Remove")
        self.summary = QLabel()
        theme.muted(self.summary)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.hide_button = QPushButton("Hide value")
        self.hidden_label = QLabel(HIDDEN)
        self.hidden_label.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.show_button = QPushButton("Show value")
        self.show_button.setToolTip("Decode and show this key only")
        self.binary_label = QLabel()
        self.binary_label.setWordWrap(True)
        self.empty_label = QLabel("Select a key, or add one.")
        theme.muted(self.empty_label)
        self.pages = QStackedWidget()
        shown = QWidget()
        shown_lay = QVBoxLayout(shown)
        shown_lay.setContentsMargins(0, 0, 0, 0)
        shown_lay.addWidget(self.editor, 1)
        if secret:
            shown_lay.addWidget(self.hide_button)
        hidden = QWidget()
        hidden_lay = QVBoxLayout(hidden)
        hidden_lay.addWidget(self.hidden_label)
        hidden_lay.addWidget(self.show_button)
        hidden_lay.addStretch(1)
        binary = QWidget()
        binary_lay = QVBoxLayout(binary)
        binary_lay.addWidget(self.binary_label)
        binary_lay.addStretch(1)
        empty = QWidget()
        empty_lay = QVBoxLayout(empty)
        empty_lay.addWidget(self.empty_label)
        empty_lay.addStretch(1)
        for widget in (empty, shown, hidden, binary):
            self.pages.addWidget(widget)  # 0 empty, 1 text, 2 hidden, 3 binary
        add_row = QHBoxLayout()
        add_row.addWidget(self.new_key, 1)
        add_row.addWidget(self.add_button)
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Keys</b>"))
        left.addWidget(self.keys, 1)
        left.addLayout(add_row)
        left.addWidget(self.remove_button)
        left.addWidget(self.summary)
        right = QVBoxLayout()
        self.value_title = QLabel("<b>Value</b>")
        right.addWidget(self.value_title)
        right.addWidget(self.pages, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left)
        lay.addLayout(right, 1)
        self._fill_keys()
        self.keys.currentRowChanged.connect(self._show_entry)
        self.keys.itemChanged.connect(self._renamed)
        self.editor.textChanged.connect(self._typed)
        self.add_button.clicked.connect(self._add)
        self.new_key.returnPressed.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.show_button.clicked.connect(self._reveal)
        self.hide_button.clicked.connect(self._conceal)
        if read_only:
            for widget in (self.new_key, self.add_button, self.remove_button):
                widget.setEnabled(False)
            self.editor.setReadOnly(True)
        if self.entries:
            self.keys.setCurrentRow(0)
        else:
            self._show_entry(-1)

    # ---- list ----
    def _fill_keys(self):
        self._loading = True
        self.keys.clear()
        for entry in self.entries:
            item = QListWidgetItem(entry["key"])
            if not self.read_only and not entry["binary"]:
                item.setFlags(item.flags() | Qt.ItemIsEditable)  # double-click to rename
            self.keys.addItem(item)
        self._loading = False
        self._summary()

    def _summary(self):
        total = sum(self._size(e) for e in self.entries)
        self.summary.setText(f"{len(self.entries)} key(s), {total / 1024:.1f} KiB of 1024 KiB")

    def _size(self, entry):
        if entry["binary"] or (self.secret and entry["text"] is None):
            return decoded_size(entry["stored"] or "")
        return len((entry["text"] or "").encode("utf-8"))

    def _current(self):
        row = self.keys.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _show_entry(self, _row):
        entry = self._current()
        self.remove_button.setEnabled(entry is not None and not self.read_only)
        if entry is None:
            self.value_title.setText("<b>Value</b>")
            self.pages.setCurrentIndex(0)
            return
        self.value_title.setText(f"<b>{escape(entry['key'])}</b>")
        if entry["binary"]:
            self.binary_label.setText(f"Binary value, {decoded_size(entry['stored'])} bytes. It is not shown and is kept as it "
                                      "is (a text editor cannot change binary data).")
            self.pages.setCurrentIndex(3)
        elif self.secret and not entry["revealed"]:
            self.pages.setCurrentIndex(2)
        else:
            self._loading = True
            self.editor.setPlainText(entry["text"] or "")
            self._loading = False
            self.pages.setCurrentIndex(1)

    def _reveal(self):
        entry = self._current()
        if entry is None:
            return
        text = decode_text(entry["stored"])  # only this key is decoded
        if text is None:
            entry["binary"] = True  # not UTF-8 text: shown as binary, kept as it is
            item = self.keys.currentItem()
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # and its name stays too
        else:
            entry["text"], entry["shown_text"], entry["revealed"] = text, text, True
        self._show_entry(self.keys.currentRow())

    def _conceal(self):
        entry = self._current()
        if entry is not None:
            entry["revealed"] = False  # edits stay in the entry; the value is just not on screen
            self._show_entry(self.keys.currentRow())

    def _typed(self):
        entry = self._current()
        if self._loading or entry is None or entry["binary"]:
            return
        entry["text"] = self.editor.toPlainText()
        self._summary()
        self.changed.emit()

    def _add(self):
        key = self.new_key.text().strip()
        if not key or any(e["key"] == key for e in self.entries):
            return
        self.entries.append({"key": key, "stored": None, "text": "", "shown_text": None, "revealed": True,
                             "binary": False, "new": True})
        self.new_key.clear()
        self._fill_keys()
        self.keys.setCurrentRow(len(self.entries) - 1)
        self.changed.emit()

    def _remove(self):
        row = self.keys.currentRow()
        if 0 <= row < len(self.entries):
            del self.entries[row]
            self._fill_keys()
            if self.entries:
                self.keys.setCurrentRow(min(row, len(self.entries) - 1))
            else:
                self._show_entry(-1)
            self.changed.emit()

    def _renamed(self, item):
        if self._loading:
            return
        row = self.keys.row(item)
        entry = self.entries[row]
        key = item.text().strip()
        if key != entry["key"] and key and not any(e["key"] == key for e in self.entries):
            entry["key"] = key
            self.value_title.setText(f"<b>{escape(key)}</b>")
            self.changed.emit()
        else:
            self._loading = True
            item.setText(entry["key"])  # an empty or repeated name is not accepted
            self._loading = False

    # ---- result ----
    def keys_in_use(self):
        return [e["key"] for e in self.entries]

    def stored_data(self):
        """({key: value to store}, {key: binary value}): untouched Secret keys keep their original base64 text."""
        data, binary = {}, {}
        for e in self.entries:
            if e["binary"] and not self.secret:
                binary[e["key"]] = e["stored"]
            elif not self.secret:
                data[e["key"]] = e["text"] or ""
            elif e["binary"] or e["text"] is None or (not e["new"] and e["text"] == e["shown_text"]):
                data[e["key"]] = e["stored"]
            else:
                data[e["key"]] = encode_text(e["text"])
        return data, binary

    def total_size(self):
        return sum(self._size(e) for e in self.entries)


def validate_data(keys, total, new=None):
    """Problems with the keys, the size and (for a new object) its name and namespace."""
    problems = []
    seen = set()
    for key in keys:
        if not valid_key(key):
            problems.append(f"The key \"{key}\" may contain only letters, digits, '-', '_' and '.'")
        elif key in seen:
            problems.append(f"The key {key} is listed twice")
        seen.add(key)
    if total > SIZE_LIMIT:
        problems.append("The values are larger than 1 MiB, the most Kubernetes accepts")
    if new is not None:
        name = new["name"].strip()
        if not name:
            problems.append("Enter a name")
        elif not NAME_RULE.fullmatch(name) or len(name) > 253:
            problems.append("The name must be lowercase letters, digits, '-' and '.'")
        if not new["namespace"].strip():
            problems.append("Enter a namespace")
    return problems


# ---- the dialog ----------------------------------------------------------------------------
class DataEditDialog(QDialog):
    """Edit or create a ConfigMap or Secret. After Save, `result_body` is the manifest (a ConfigMap, or a new Secret); for an
    existing Secret it is the merge *patch* (`is_patch`), which may be empty when nothing changed."""

    def __init__(self, kind, raw, parent=None, secret_data=None, create=False, namespaces=()):
        super().__init__(parent)
        self.kind, self._raw, self.create = kind, raw, create
        self.secret = kind == "Secret"
        self.is_patch = self.secret and not create
        self.result_body, self.wants_yaml, self.pending = None, False, None
        meta = raw.get("metadata") or {}
        immutable = bool(raw.get("immutable")) and not create
        self.setWindowTitle(f"New {kind}" if create else f"Edit {kind} {meta.get('name', '')}")
        self.resize(860, 640)
        if self.secret:
            stored = dict(secret_data or {})
            binary = None
            self._original_data = dict(stored)  # what the patch is measured against
        else:
            stored, binary = dict(raw.get("data") or {}), dict(raw.get("binaryData") or {})
        intro = f"New {escape(kind)}. " if create else (f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                                                       f"<b>{escape(str(meta.get('namespace', '')))}</b>. ")
        if self.secret:
            intro += SHOW_NOTE
        else:
            intro += "Binary values are kept as they are."
        self.head = QLabel(intro)
        self.head.setWordWrap(True)
        notes = []
        if immutable:
            notes.append("This object is immutable: its data cannot be changed (delete it and create it again). Labels and "
                         "annotations can.")
        if self.secret and raw.get("type") == "kubernetes.io/service-account-token":
            notes.append("This is a service account token: Kubernetes manages it, and a change to its data is overwritten.")
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(n) for n in notes))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(notes))

        self.data = DataEditor(stored, self.secret, binary, read_only=immutable)
        info = QFormLayout()
        self.name_edit = self.namespace_edit = self.type_box = None
        if create:
            self.name_edit = QLineEdit(placeholderText="lowercase letters, digits, - and .")
            self.namespace_edit = QComboBox()
            self.namespace_edit.setEditable(True)
            self.namespace_edit.addItems(list(namespaces))
            self.namespace_edit.setCurrentText(str(meta.get("namespace", "")))
            info.addRow("Name", self.name_edit)
            info.addRow("Namespace", self.namespace_edit)
        self.type_note = QLabel()
        self.type_note.setWordWrap(True)
        theme.muted(self.type_note)
        if self.secret and create:
            self.type_box = QComboBox()
            self.type_box.addItems(list(SECRET_TYPES))
            self.type_box.currentTextChanged.connect(lambda t: self.type_note.setText(SECRET_TYPES.get(t, "")))
            self.type_note.setText(SECRET_TYPES["Opaque"])
            info.addRow("Type", self.type_box)
            info.addRow("", self.type_note)
        elif self.secret:
            info.addRow("Type", QLabel(escape(str(raw.get("type", "Opaque")))))  # cannot be changed after creation
        data_tab = QWidget()
        data_lay = QVBoxLayout(data_tab)
        data_lay.addLayout(info)
        data_lay.addWidget(self.data, 1)

        self.annotations = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,), height=110)
        self.labels = KeyValueEditor("Labels", height=110)
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))
        meta_tab = QWidget()
        meta_lay = QVBoxLayout(meta_tab)
        meta_lay.addWidget(self.labels)
        meta_lay.addWidget(self.annotations)
        meta_lay.addStretch(1)
        self.sections = QTabWidget()
        self.sections.addTab(data_tab, "Data")
        self.sections.addTab(meta_tab, "Labels and annotations")

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.yaml_button = QPushButton("Edit as YAML…")
        self.yaml_button.setVisible(not self.secret)  # a Secret's YAML would only hold the masked placeholder
        self.yaml_button.setToolTip("Continue in YAML with the changes made here")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Create" if create else "Save")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.yaml_button)
        bottom.addWidget(buttons)
        outer = QVBoxLayout(self)
        outer.addWidget(self.head)
        outer.addWidget(self.banner)
        outer.addWidget(self.sections, 1)
        outer.addLayout(bottom)
        self.yaml_button.clicked.connect(self._as_yaml)
        for editor in (self.data, self.labels, self.annotations):
            editor.changed.connect(self._hint)
        for edit in (self.name_edit,):
            if edit is not None:
                edit.textChanged.connect(self._hint)
        if self.namespace_edit is not None:
            self.namespace_edit.currentTextChanged.connect(self._hint)
        self._hint()

    # ---- result ----
    def _new(self):
        if not self.create:
            return None
        return {"name": self.name_edit.text(), "namespace": self.namespace_edit.currentText()}

    def problems(self):
        return (validate_data(self.data.keys_in_use(), self.data.total_size(), self._new())
                + validate_pairs(self.labels.pairs(), "label") + validate_pairs(self.annotations.pairs(), "annotation"))

    def _hint(self):
        problems = self.problems()
        self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(problems[0])}</span>"
                          + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "") if problems else "")

    def _body(self):
        """The new manifest (for a Secret that exists: the patch)."""
        data, binary = self.data.stored_data()
        meta = {"labels": self.labels.items(), "annotations": self.annotations.items()}
        if self.is_patch:
            return secret_patch(self._original_data, data, self._raw.get("metadata") or {}, meta)
        body = deepcopy(self._raw)
        m = body.setdefault("metadata", {})
        new = self._new()
        if new:
            m["name"], m["namespace"] = new["name"].strip(), new["namespace"].strip()
        for key, value in meta.items():
            if value:
                m[key] = value
            else:
                m.pop(key, None)
        if self.secret:
            body.setdefault("apiVersion", "v1")
            body["kind"] = "Secret"
            body["type"] = self.type_box.currentText()
            self._set(body, "data", data)
        else:
            self._set(body, "data", data if not self.data.read_only else dict(self._raw.get("data") or {}))
            self._set(body, "binaryData", binary)
        return body

    @staticmethod
    def _set(body, key, value):
        if value:
            body[key] = value
        else:
            body.pop(key, None)

    def _as_yaml(self):
        leave_for_yaml(self)

    def pending_body(self):
        """A ConfigMap's manifest as the form is now (a Secret has no YAML view)."""
        if self.secret:
            return None
        try:
            return self._body()
        except (ValueError, KeyError, TypeError):
            return None

    def _save(self):
        problems = self.problems()
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        body = self._body()
        if self.is_patch and not body:
            QMessageBox.information(self, "Nothing to save", "Nothing was changed.")
            self.reject()
            return
        self.result_body = body
        self.accept()
