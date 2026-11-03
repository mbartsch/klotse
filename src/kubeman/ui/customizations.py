import json

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)


DEFAULT_LABELS = {"Node": ["karpenter.sh/nodepool"], "Pod": []}
# built-in columns hidden until the user ticks them (used when no layout was saved for the kind)
DEFAULT_HIDDEN = {"Pod": ["cpu_req", "cpu_lim", "cpu_req_pct", "cpu_lim_pct",
                          "mem_req", "mem_lim", "mem_req_pct", "mem_lim_pct"]}


def normalize_label_keys(keys):
    result = []
    for value in keys:
        key = value.strip()
        if not key:
            continue
        if "=" in key or any(char.isspace() for char in key):
            raise ValueError("Enter label keys without values, one per line.")
        if key not in result:
            result.append(key)
    return result


def parse_label_columns(lines):
    keys, headers = [], {}
    for line in lines:
        key, _, heading = line.partition("=")
        key = key.strip()
        if not key:
            if line.strip():
                raise ValueError("A column heading must have a label key before '='.")
            continue
        keys.append(key)
        if heading.strip():
            headers[key] = heading.strip()
        else:
            headers.pop(key, None)
    return normalize_label_keys(keys), headers


SECTION = "columns."  # one INI section per resource kind, e.g. [columns.Pod]
SEP = "|"  # list separator; QSettings quotes values containing , ; or =, so those would make the file noisy
ARROW = "->"  # label key -> heading inside the file (the dialog still uses "key = heading")


def _join(items):
    return f" {SEP} ".join(items)


def _split(text):
    return [part.strip() for part in str(text).split(SEP) if part.strip()]


class LabelSettings(QObject):
    """Column preferences per resource kind, saved as readable INI sections:

        [columns.Pod]
        order=name; namespace; ready          built-in column order (omitted = default)
        hidden=cpu_req; cpu_lim               built-in columns that are hidden (omitted = default)
        labels=app.kubernetes.io/name = App   label columns, optional "= heading" (omitted = default)

    An empty value (`hidden=`) means "none", an absent key means "use the default".
    Files written by earlier versions (JSON under columns/labels and columns/layout) are still read and are
    rewritten in this format the next time something is saved.
    """

    changed = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else QSettings(
            QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman")
        self._labels = {kind: list(keys) for kind, keys in DEFAULT_LABELS.items()}
        self._headers = {}
        self._layout = {}
        self._explicit_labels = set()  # kinds whose label list was chosen by the user (written to the file)
        self._load_sections()
        self._load_legacy()

    # ---- reading ----------------------------------------------------------
    def _load_sections(self):
        for group in self.store.childGroups():
            if not group.startswith(SECTION) or len(group) == len(SECTION):
                continue
            kind = group[len(SECTION):]
            self.store.beginGroup(group)
            try:
                order, hidden, labels = (self.store.value(name, None) for name in ("order", "hidden", "labels"))
            finally:
                self.store.endGroup()
            if order is not None or hidden is not None:
                self._layout[kind] = {
                    "order": _split(order) if order is not None else [],
                    "hidden": _split(hidden) if hidden is not None else list(DEFAULT_HIDDEN.get(kind, []))}
            if labels is not None:
                try:
                    keys, headers = parse_label_columns(
                        [entry.replace(ARROW, "=", 1) for entry in _split(labels)])
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
                        key: title.strip().replace(SEP, "/") for key, title in aliases.items()
                        if key in self._labels[kind] and isinstance(title, str) and title.strip()
                        and "\n" not in title and "\r" not in title}

    _legacy = False

    # ---- access -----------------------------------------------------------
    def labels(self, kind):
        return list(self._labels.get(kind, []))

    def headers(self, kind):
        return dict(self._headers.get(kind, {}))

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
            if SEP in title:
                raise ValueError(f"Column headings cannot contain '{SEP}'.")
        updated_headers = {key: title.strip() for key, title in aliases.items() if key in keys and title.strip()}
        self._save(labels={**self._labels, kind: keys}, headers={**self._headers, kind: updated_headers},
                   explicit=self._explicit_labels | {kind})

    def set_layout(self, kind, order, hidden):
        if not kind:
            raise ValueError(f"Unsupported column kind: {kind}")
        self._save(layout={**self._layout, kind: {"order": list(order), "hidden": list(hidden)}})

    def _save(self, labels=None, headers=None, layout=None, explicit=None):
        labels = self._labels if labels is None else labels
        headers = self._headers if headers is None else headers
        layout = self._layout if layout is None else layout
        explicit = self._explicit_labels if explicit is None else explicit
        for group in self.store.childGroups():  # rewrite every column section from scratch
            if group.startswith(SECTION):
                self.store.remove(group)
        for old in ("columns/labels", "columns/layout"):  # earlier JSON format
            self.store.remove(old)
        for kind in sorted(set(layout) | explicit):
            self.store.beginGroup(SECTION + kind)
            if kind in layout:
                self.store.setValue("order", _join(layout[kind].get("order", [])))
                self.store.setValue("hidden", _join(layout[kind].get("hidden", [])))
            if kind in explicit:
                titles = headers.get(kind, {})
                self.store.setValue("labels", _join(
                    f"{key} {ARROW} {titles[key]}" if key in titles else key for key in labels.get(kind, [])))
            self.store.endGroup()
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save customizations to {self.store.fileName()}")
        self._labels, self._headers, self._layout, self._explicit_labels = labels, headers, layout, explicit
        self._legacy = False
        self.changed.emit()


class LabelColumnsDialog(QDialog):
    def __init__(self, kind, settings, available=(), builtin=(), parent=None):
        super().__init__(parent)
        self.kind, self.settings = kind, settings
        self.setWindowTitle(f"Customizations — {kind} columns")
        self.resize(650, 560)
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
        self.available = QComboBox()
        self.available.setEditable(True)
        self.available.setInsertPolicy(QComboBox.NoInsert)
        self.available.setMaxVisibleItems(12)
        self.available.addItems(sorted(available))
        self.available.setCurrentIndex(-1)
        self.available.lineEdit().setPlaceholderText("Choose or enter a label key…")
        self.available.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.available.completer().setFilterMode(Qt.MatchContains)
        self.available.completer().setCaseSensitivity(Qt.CaseInsensitive)
        add = QPushButton("Add label")
        add.clicked.connect(self._add)
        picker = QHBoxLayout()
        picker.addWidget(self.available, 1)
        self.heading = QLineEdit(placeholderText="Short heading (optional)")
        picker.addWidget(self.heading, 1)
        picker.addWidget(add)
        layout.addLayout(picker)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("karpenter.sh/nodepool = Pool\nexample.com/team = Team")
        headings = settings.headers(kind)
        self.editor.setPlainText("\n".join(
            f"{key} = {headings[key]}" if key in headings else key for key in settings.labels(kind)))
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)
        layout.addWidget(buttons)

    def _restore(self):
        self.editor.setPlainText("\n".join(DEFAULT_LABELS.get(self.kind, [])))
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
        key = self.available.currentText().strip()
        heading = self.heading.text().strip()
        entry = f"{key} = {heading}" if heading else key
        if not key:
            return
        lines = self.editor.toPlainText().splitlines()
        for i, line in enumerate(lines):
            if line.partition("=")[0].strip() == key:
                if heading:
                    lines[i] = entry
                    self.editor.setPlainText("\n".join(lines))
                return
        if key:
            text = self.editor.toPlainText().rstrip()
            self.editor.setPlainText(text + ("\n" if text else "") + entry)

    def _save(self):
        try:
            keys, headings = parse_label_columns(self.editor.toPlainText().splitlines())
            if self.builtin.count():
                self.settings.set_layout(self.kind, *self.builtin_layout())
            self.settings.set_labels(self.kind, keys, headings)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot save customizations", str(error))
            return
        self.accept()
