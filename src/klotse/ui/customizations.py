import json
import re

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from .settings_store import default_store


DEFAULT_LABELS = {"Node": ["karpenter.sh/nodepool"], "Pod": []}
# built-in columns hidden until the user ticks them (used when no layout was saved for the kind)
DEFAULT_HIDDEN = {"Pod": ["cpu_req", "cpu_lim", "cpu_req_pct", "cpu_lim_pct",
                          "mem_req", "mem_lim", "mem_req_pct", "mem_lim_pct"]}


# kinds whose Columns… dialog also offers annotation columns (the RBAC lists; any kind works in the settings file)
ANNOTATION_KINDS = frozenset({"Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "ServiceAccount"})


def normalize_label_keys(keys, what="label"):
    result = []
    for value in keys:
        key = value.strip()
        if not key:
            continue
        if "=" in key or any(char.isspace() for char in key):
            raise ValueError(f"Enter {what} keys without values, one per line.")
        if key not in result:
            result.append(key)
    return result


def parse_label_columns(lines, what="label"):
    keys, headers = [], {}
    for line in lines:
        key, _, heading = line.partition("=")
        key = key.strip()
        if not key:
            if line.strip():
                raise ValueError(f"A column heading must have a {what} key before '='.")
            continue
        keys.append(key)
        if heading.strip():
            headers[key] = heading.strip()
        else:
            headers.pop(key, None)
    return normalize_label_keys(keys, what), headers


SECTION = "columns."  # one INI section per resource kind, e.g. [columns.Pod]
ARROW = "->"  # the previous format wrote a heading as `key -> heading` (the dialog uses "key = heading")
LEGACY_SEP = "|"  # the previous file format kept a whole list on one line: `hidden=a | b | c`


def _numbered(store, name):
    """{index: value} for the keys `name.0`, `name.1`, ... in the current group (empty values are skipped)."""
    found = {}
    for key in store.childKeys():
        match = re.fullmatch(re.escape(name) + r"\.(\d+)", key)
        if match:
            value = str(store.value(key, "")).strip()
            if value:
                found[int(match[1])] = value
    return found


def _read_list(store, name):
    """Read a list stored as `name.0`, `name.1`, ... in the current group.

    Returns (found, items, old_format). A bare empty `name=` is an explicit empty list; the older single-line
    `name=a | b | c` form is still understood (old_format=True) so the file can be rewritten.
    """
    numbered = _numbered(store, name)
    if numbered:
        return True, [value for _, value in sorted(numbered.items())], False
    bare = store.value(name, None)
    if bare is None:
        return False, [], False
    text = str(bare).strip()
    if not text:
        return True, [], False
    return True, [part.strip() for part in text.split(LEGACY_SEP) if part.strip()], True


def _read_labels(store, name="labels"):
    """Read the label (or, with name="annotations", annotation) columns as (found, lines, old_format), with lines in
    the dialog's `key = heading` syntax.

    `labels.N` holds the key and `labels_name.N` (same N) its optional heading. The previous formats, a
    heading written as `key -> heading` in the value or a whole list on one line, are still read.
    """
    numbered = sorted(_numbered(store, name).items())
    if numbered:
        names = _numbered(store, f"{name}_name")
        lines, old = [], False
        for index, value in numbered:
            if ARROW in value:
                value, old = value.replace(ARROW, "=", 1), True
            elif index in names:
                value = f"{value} = {names[index]}"
            lines.append(value)
        return True, lines, old
    found, items, old = _read_list(store, name)
    return found, [item.replace(ARROW, "=", 1) for item in items], old


def _write_list(store, name, items, keep_empty=True):
    """Write items as `name.0`, `name.1`, ...; an empty list is a bare `name=` (so "none" differs from "default")."""
    for index, item in enumerate(items):
        store.setValue(f"{name}.{index}", item)
    if not items and keep_empty:
        store.setValue(name, "")


class LabelSettings(QObject):
    """Column preferences per resource kind, saved as readable INI sections, one entry per line:

        [columns.Pod]
        order.0=name                            built-in column order (omitted = default order)
        order.1=namespace
        hidden.0=cpu_req                        built-in columns that are hidden (omitted = the default set)
        hidden.1=cpu_lim
        labels.0=app.kubernetes.io/name         label columns (omitted = the default labels)
        labels_name.0=App                       optional heading for labels.0
        labels.1=example.com/team
        annotations.0=example.com/owner         annotation columns (RBAC lists; omitted = none)
        annotations_name.0=Owner                optional heading for annotations.0

    A bare empty key (`hidden=`, `labels=`) means "none"; a key that is absent means "use the default".
    Older files are still read and are rewritten in this format when the app starts: the JSON values under
    columns/labels and columns/layout, the `a | b | c` single-line lists and `key -> heading` values.
    """

    changed = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else default_store()
        self._labels = {kind: list(keys) for kind, keys in DEFAULT_LABELS.items()}
        self._headers = {}
        self._layout = {}
        self._explicit_labels = set()  # kinds whose label list was chosen by the user (written to the file)
        self._annotations, self._annotation_headers, self._explicit_annotations = {}, {}, set()
        self._load_sections()
        self._load_legacy()
        if self._legacy:  # an older file: rewrite it right away so the file on disk is readable
            try:
                self._save()
            except OSError:
                pass  # read-only settings: keep working from memory; a later save reports the error

    # ---- reading ----------------------------------------------------------
    def _load_sections(self):
        for group in self.store.childGroups():
            if not group.startswith(SECTION) or len(group) == len(SECTION):
                continue
            kind = group[len(SECTION):]
            self.store.beginGroup(group)
            try:
                order_found, order, order_old = _read_list(self.store, "order")
                hidden_found, hidden, hidden_old = _read_list(self.store, "hidden")
                labels_found, labels, labels_old = _read_labels(self.store)
                notes_found, notes, notes_old = _read_labels(self.store, "annotations")
            finally:
                self.store.endGroup()
            if notes_found:
                try:
                    keys, headers = parse_label_columns(notes, "annotation")
                    self._annotations[kind], self._annotation_headers[kind] = keys, headers
                    self._explicit_annotations.add(kind)
                except ValueError:
                    pass  # an invalid annotation list is ignored, like an invalid label list
            if order_old or hidden_old or labels_old:
                self._legacy = True  # an older layout: rewrite it in the current one
            if order_found or hidden_found:
                self._layout[kind] = {"order": order,
                                      "hidden": hidden if hidden_found else list(DEFAULT_HIDDEN.get(kind, []))}
            if labels_found:
                try:
                    keys, headers = parse_label_columns(labels)
                except ValueError:
                    continue
                self._labels[kind], self._headers[kind] = keys, headers
                self._explicit_labels.add(kind)

    def _load_legacy(self):
        """Read the JSON values of earlier versions for kinds the new sections did not define."""
        def blob(name):
            try:
                value = json.loads(self.store.value(name, "{}"))
            except (ValueError, TypeError):
                return {}
            return value if isinstance(value, dict) else {}

        saved, layout = blob("columns/labels"), blob("columns/layout")
        if saved or layout:
            self._legacy = True
        for kind, entry in layout.items():
            if kind in self._layout:
                continue
            if isinstance(entry, dict) and all(
                    isinstance(entry.get(f, []), list) and all(isinstance(a, str) for a in entry.get(f, []))
                    for f in ("order", "hidden")):
                self._layout[kind] = {"order": entry.get("order", []), "hidden": entry.get("hidden", [])}
        headers = saved.get("headers", {})
        for kind, keys in saved.items():
            if kind == "headers" or kind in self._explicit_labels:
                continue
            if isinstance(keys, list) and all(isinstance(k, str) for k in keys):
                try:
                    self._labels[kind] = normalize_label_keys(keys)
                except ValueError:
                    continue
                self._explicit_labels.add(kind)
                aliases = headers.get(kind, {}) if isinstance(headers, dict) else {}
                if isinstance(aliases, dict):
                    self._headers[kind] = {
                        key: title.strip() for key, title in aliases.items()
                        if key in self._labels[kind] and isinstance(title, str) and title.strip()
                        and "\n" not in title and "\r" not in title}

    _legacy = False

    # ---- access -----------------------------------------------------------
    def labels(self, kind):
        return list(self._labels.get(kind, []))

    def headers(self, kind):
        return dict(self._headers.get(kind, {}))

    def annotations(self, kind):
        return list(self._annotations.get(kind, []))

    def annotation_headers(self, kind):
        return dict(self._annotation_headers.get(kind, {}))

    def layout(self, kind):
        """Saved built-in column order and hidden attributes for a kind: {"order": [...], "hidden": [...]}."""
        saved = self._layout.get(kind)
        if saved is None:
            return {"order": [], "hidden": list(DEFAULT_HIDDEN.get(kind, []))}
        return {"order": list(saved.get("order", [])), "hidden": list(saved.get("hidden", []))}

    # ---- writing ----------------------------------------------------------
    def set_labels(self, kind, keys, headers=None):
        if not kind or kind == "headers":
            raise ValueError(f"Unsupported label column kind: {kind}")
        keys = normalize_label_keys(keys)
        aliases = self._headers.get(kind, {}) if headers is None else headers
        for title in aliases.values():
            if not isinstance(title, str) or "\n" in title or "\r" in title:
                raise ValueError("Column headings must be single-line text.")
        updated_headers = {key: title.strip() for key, title in aliases.items() if key in keys and title.strip()}
        self._save(labels={**self._labels, kind: keys}, headers={**self._headers, kind: updated_headers},
                   explicit=self._explicit_labels | {kind})

    def set_annotations(self, kind, keys, headers=None):
        """Annotation columns of a kind (same rules as set_labels; saved next to the labels)."""
        if not kind:
            raise ValueError(f"Unsupported annotation column kind: {kind}")
        keys = normalize_label_keys(keys, "annotation")
        aliases = self._annotation_headers.get(kind, {}) if headers is None else headers
        for title in aliases.values():
            if not isinstance(title, str) or "\n" in title or "\r" in title:
                raise ValueError("Column headings must be single-line text.")
        updated = {key: title.strip() for key, title in aliases.items() if key in keys and title.strip()}
        self._save(annotations={**self._annotations, kind: keys},
                   annotation_headers={**self._annotation_headers, kind: updated},
                   explicit_annotations=self._explicit_annotations | {kind})

    def set_layout(self, kind, order, hidden):
        if not kind:
            raise ValueError(f"Unsupported column kind: {kind}")
        self._save(layout={**self._layout, kind: {"order": list(order), "hidden": list(hidden)}})

    def _save(self, labels=None, headers=None, layout=None, explicit=None, annotations=None,
              annotation_headers=None, explicit_annotations=None):
        annotations = self._annotations if annotations is None else annotations
        annotation_headers = self._annotation_headers if annotation_headers is None else annotation_headers
        explicit_annotations = self._explicit_annotations if explicit_annotations is None else explicit_annotations
        labels = self._labels if labels is None else labels
        headers = self._headers if headers is None else headers
        layout = self._layout if layout is None else layout
        explicit = self._explicit_labels if explicit is None else explicit
        for group in self.store.childGroups():  # rewrite every column section from scratch
            if group.startswith(SECTION):
                self.store.remove(group)
        for old in ("columns/labels", "columns/layout"):  # earlier JSON format
            self.store.remove(old)
        for kind in sorted(set(layout) | explicit | explicit_annotations):
            self.store.beginGroup(SECTION + kind)
            if kind in layout:
                _write_list(self.store, "order", layout[kind].get("order", []), keep_empty=False)  # none = default
                _write_list(self.store, "hidden", layout[kind].get("hidden", []))
            if kind in explicit:
                titles = headers.get(kind, {})
                keys = labels.get(kind, [])
                _write_list(self.store, "labels", keys)
                for index, key in enumerate(keys):
                    if key in titles:
                        self.store.setValue(f"labels_name.{index}", titles[key])
            if kind in explicit_annotations:
                titles = annotation_headers.get(kind, {})
                keys = annotations.get(kind, [])
                _write_list(self.store, "annotations", keys)
                for index, key in enumerate(keys):
                    if key in titles:
                        self.store.setValue(f"annotations_name.{index}", titles[key])
            self.store.endGroup()
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save customizations to {self.store.fileName()}")
        self._labels, self._headers, self._layout, self._explicit_labels = labels, headers, layout, explicit
        self._annotations, self._annotation_headers = annotations, annotation_headers
        self._explicit_annotations = explicit_annotations
        self._legacy = False
        self.changed.emit()


def _add_entry(combo, heading, editor):
    """Add the key chosen in `combo` (with the optional heading) as a line of `editor`; an existing key keeps its place
    and only takes a new heading."""
    key = combo.currentText().strip()
    heading = heading.text().strip()
    entry = f"{key} = {heading}" if heading else key
    if not key:
        return
    lines = editor.toPlainText().splitlines()
    for i, line in enumerate(lines):
        if line.partition("=")[0].strip() == key:
            if heading:
                lines[i] = entry
                editor.setPlainText("\n".join(lines))
            return
    text = editor.toPlainText().rstrip()
    editor.setPlainText(text + ("\n" if text else "") + entry)


class LabelColumnsDialog(QDialog):
    def __init__(self, kind, settings, available=(), builtin=(), parent=None, available_annotations=()):
        super().__init__(parent)
        self.kind, self.settings = kind, settings
        self.setWindowTitle(f"Customizations — {kind} columns")
        self.resize(650, 700 if kind in ANNOTATION_KINDS else 560)
        layout = QVBoxLayout(self)
        self.builtin = QListWidget()
        self.builtin.setMaximumHeight(170)
        if builtin:
            titles = {attr: header for header, attr, *_ in builtin}
            saved = settings.layout(kind)
            first = builtin[0][1]  # the first column (Name) is always shown and stays first
            rest = [attr for _, attr, *_ in builtin[1:]]
            order = [a for a in saved["order"] if a in rest] + [a for a in rest if a not in saved["order"]]
            for attr in [first] + order:
                item = QListWidgetItem(titles[attr])
                item.setData(Qt.UserRole, attr)
                if attr == first:
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setCheckState(Qt.Checked)
                else:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Unchecked if attr in saved["hidden"] else Qt.Checked)
                self.builtin.addItem(item)
            self._default_builtin = [b[1] for b in builtin]
            layout.addWidget(QLabel("Built-in columns: untick to hide, use Up/Down to reorder."))
            layout.addWidget(self.builtin)
            moves = QHBoxLayout()
            for text, delta in (("Move up", -1), ("Move down", 1)):
                button = QPushButton(text)
                button.clicked.connect(lambda _=False, d=delta: self._move(d))
                moves.addWidget(button)
            moves.addStretch(1)
            layout.addLayout(moves)
        else:
            self.builtin.hide()
        hint = QLabel("One label key per line, in column order. Use key = Short heading to rename a column.\n"
                      "Leave empty to hide label columns.\n"
                      "Saved for all clusters and future sessions.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.available, self.heading, self.editor = self._key_section(
            layout, "label", available, settings.labels(kind), settings.headers(kind),
            "karpenter.sh/nodepool = Pool\nexample.com/team = Team")
        # RBAC lists can show annotations as columns too (they sit right after the label columns)
        self.annotation_available = self.annotation_heading = self.annotation_editor = None
        if kind in ANNOTATION_KINDS:
            note = QLabel("Annotation columns work the same way: one annotation key per line. Long values are cut "
                          "in the table and shown in full in the tooltip.")
            note.setWordWrap(True)
            layout.addWidget(note)
            self.annotation_available, self.annotation_heading, self.annotation_editor = self._key_section(
                layout, "annotation", available_annotations, settings.annotations(kind),
                settings.annotation_headers(kind), "example.com/owner = Owner")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)
        layout.addWidget(buttons)

    def _key_section(self, layout, noun, available, current, headings, example):
        """A combo box of known keys (searchable, free text allowed), a heading box, an Add button and the text box
        with one `key = heading` line per column. Returns (combo, heading, editor)."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMaxVisibleItems(12)
        combo.addItems(sorted(available))
        combo.setCurrentIndex(-1)
        combo.lineEdit().setPlaceholderText(f"Choose or enter {'an' if noun == 'annotation' else 'a'} {noun} key…")
        combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        combo.completer().setFilterMode(Qt.MatchContains)
        combo.completer().setCaseSensitivity(Qt.CaseInsensitive)
        heading = QLineEdit(placeholderText="Short heading (optional)")
        editor = QPlainTextEdit()
        editor.setPlaceholderText(example)
        editor.setPlainText("\n".join(f"{key} = {headings[key]}" if key in headings else key for key in current))
        add = QPushButton(f"Add {noun}")
        add.clicked.connect(lambda: _add_entry(combo, heading, editor))
        picker = QHBoxLayout()
        picker.addWidget(combo, 1)
        picker.addWidget(heading, 1)
        picker.addWidget(add)
        layout.addLayout(picker)
        layout.addWidget(editor)
        return combo, heading, editor

    def _restore(self):
        self.editor.setPlainText("\n".join(DEFAULT_LABELS.get(self.kind, [])))
        if self.annotation_editor is not None:
            self.annotation_editor.setPlainText("")
        by_attr = {self.builtin.item(i).data(Qt.UserRole): self.builtin.takeItem(i)
                   for i in reversed(range(self.builtin.count()))}
        for attr in getattr(self, "_default_builtin", []):
            item = by_attr[attr]
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Unchecked if attr in DEFAULT_HIDDEN.get(self.kind, []) else Qt.Checked)
            self.builtin.addItem(item)

    def _move(self, delta):
        row = self.builtin.currentRow()
        target = row + delta
        if row < 1 or not 1 <= target < self.builtin.count():  # row 0 (Name) is fixed
            return
        item = self.builtin.takeItem(row)
        self.builtin.insertItem(target, item)
        self.builtin.setCurrentRow(target)

    def builtin_layout(self):
        """(order, hidden) of the built-in columns as currently shown in the list."""
        items = [self.builtin.item(i) for i in range(self.builtin.count())]
        return ([i.data(Qt.UserRole) for i in items],
                [i.data(Qt.UserRole) for i in items if i.checkState() == Qt.Unchecked])

    def _add(self):
        _add_entry(self.available, self.heading, self.editor)

    def _add_annotation(self):
        if self.annotation_editor is not None:
            _add_entry(self.annotation_available, self.annotation_heading, self.annotation_editor)

    def _save(self):
        try:
            keys, headings = parse_label_columns(self.editor.toPlainText().splitlines())
            notes = None
            if self.annotation_editor is not None:  # parse everything first so a mistake saves nothing
                notes = parse_label_columns(self.annotation_editor.toPlainText().splitlines(), "annotation")
            if self.builtin.count():
                self.settings.set_layout(self.kind, *self.builtin_layout())
            self.settings.set_labels(self.kind, keys, headings)
            if notes is not None:
                self.settings.set_annotations(self.kind, *notes)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot save customizations", str(error))
            return
        self.accept()
