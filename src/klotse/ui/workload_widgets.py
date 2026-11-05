"""Widgets of the workload editor: an editable rows table, probes, container and pod forms. The data rules live in
workload_model.py; these classes only show the manifest's values and give them back (`values()`)."""
from copy import deepcopy

from PySide6.QtCore import Qt, QStringListModel, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QCompleter, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QStyledItemDelegate, QTableWidget,
                               QStyle, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .service_account_editor import KeyValueEditor
from .workload_model import (CONTAINER_FLAGS, CONTAINER_IDS, OTHER_VOLUME, RESTART_POLICIES, PROBE_MODES, PROBE_NAMES, PROBE_TIMINGS,
                             PROTOCOLS, PULL_POLICIES, RESOURCES, TAINT_EFFECTS, TOLERATION_OPERATORS, VOLUME_TYPES,
                             ENV_ADD_HINTS, ENV_FIELD_PATHS, ENV_FROM_SOURCES, ENV_RESOURCES, ENV_SOURCES, build_env_entry, build_env_from,
                             build_volume, env_from_row, env_ref, probe_values,
                             volume_row)


class ComboDelegate(QStyledItemDelegate):
    """A drop-down for a table column with a fixed list of values (the first, empty one means "not set")."""

    def __init__(self, choices, parent=None, editable=False):
        super().__init__(parent)
        self.choices = choices  # a list can be replaced later (RowsEditor.set_choices) for editable columns
        self.editable = editable

    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        box.setEditable(self.editable)
        box.addItems(list(self.choices))
        return box

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data() or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


def default_load(columns):
    def load(entry):
        out = {}
        for key, _title, kind, _choices in columns:
            value = entry.get(key)
            out[key] = bool(value) if kind == "bool" else ("" if value is None else str(value))
        return out
    return load


def default_dump(columns):
    def dump(original, texts):
        entry = deepcopy(original or {})
        for key, _title, kind, _choices in columns:
            value = texts[key]
            if kind == "bool":
                if value:
                    entry[key] = True
                else:
                    entry.pop(key, None)
                continue
            text = str(value).strip()
            if not text:
                entry.pop(key, None)
            elif kind in ("int", "port"):  # "port": a number, or a port name that stays text
                entry[key] = int(text) if text.isdigit() else text  # a bad number stays text: validation reports it
            else:
                entry[key] = text
        return entry
    return dump


class RowsEditor(QWidget):
    """A table of dict entries (ports, tolerations, mounts, volumes...). `columns` are (key, heading, kind, choices) with kind
    text / int / bool / choice. An existing entry keeps every field the table does not show; `load(entry)` gives the
    cell texts and `dump(original, texts)` the entry back (defaults: the column key is the entry key)."""

    changed = Signal()

    def set_choices(self, key, values):
        """Offer `values` in the drop-down of a "suggest" column; anything else can still be typed."""
        for col, column in enumerate(self.columns):
            if column[0] == key:
                delegate = self.table.itemDelegateForColumn(col)
                if isinstance(delegate, ComboDelegate):
                    delegate.choices = ["", *values]

    def __init__(self, title, columns, entries, new_entry=None, add_text="Add", load=None, dump=None,
                 existing_readonly=(), height=110, note=""):
        super().__init__()
        self.columns = columns
        self.new_entry = new_entry or {}
        self.load = load or default_load(columns)
        self.dump = dump or default_dump(columns)
        self.existing_readonly = existing_readonly
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c[1] for c in columns])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(height)
        for col, (_key, _title, kind, choices) in enumerate(columns):
            if kind == "choice":
                self.table.setItemDelegateForColumn(col, ComboDelegate(choices, self.table))
            elif kind == "suggest":  # text with a drop-down of values known in the cluster (set_choices)
                self.table.setItemDelegateForColumn(col, ComboDelegate([], self.table, editable=True))
        self.add_button = QPushButton(add_text)
        self.remove_button = QPushButton("Remove selected")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if title:
            lay.addWidget(QLabel(f"<b>{title}</b>"))
        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            theme.muted(hint)
            lay.addWidget(hint)
        lay.addWidget(self.table)
        lay.addLayout(buttons)
        self._loading = True
        for entry in entries:
            self._append(entry, existing=True)
        self._loading = False
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.table.itemChanged.connect(lambda _item: None if self._loading else self.changed.emit())
        self._existing_locked = False
        self.table.itemSelectionChanged.connect(self._update_remove)

    def lock_existing(self, why="These entries already exist and cannot be changed or removed; new ones can be added."):
        """Rows that were already there become read-only and cannot be removed (a running pod only accepts additions); rows
        added afterwards stay editable. Remove is greyed out unless only added rows are selected."""
        self._existing_locked, self._lock_why = True, why
        for r in range(self.table.rowCount()):
            if self._is_existing(r):
                for c in range(self.table.columnCount()):
                    item = self.table.item(r, c)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsUserCheckable)
        self._update_remove()

    def _is_existing(self, row):
        item = self.table.item(row, 0)
        return item is not None and item.data(Qt.UserRole) is not None

    def _update_remove(self):
        if not self._existing_locked:
            return
        rows = {i.row() for i in self.table.selectedIndexes()}
        usable = bool(rows) and not any(self._is_existing(r) for r in rows)
        self.remove_button.setEnabled(usable)
        self.remove_button.setToolTip("" if usable else self._lock_why)

    def _append(self, entry, existing):
        texts = self.load(entry)
        row = self.table.rowCount()
        self.table.setRowCount(row + 1)
        for col, (key, _title, kind, _choices) in enumerate(self.columns):
            item = QTableWidgetItem()
            if kind == "bool":
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if texts[key] else Qt.Unchecked)
            else:
                item.setText(str(texts[key]))
            if existing and key in self.existing_readonly:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if col == 0:
                item.setData(Qt.UserRole, deepcopy(entry) if existing else None)
            self.table.setItem(row, col, item)

    def _add(self):
        self._loading = True
        self._append(deepcopy(self.new_entry), existing=False)
        self._loading = False
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))
        self.changed.emit()

    def _remove(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            if not (self._existing_locked and self._is_existing(row)):
                self.table.removeRow(row)
        self.changed.emit()

    def count(self):
        return self.table.rowCount()

    def texts(self):
        """The cells as typed, one dict per row (before they are turned into entries, which drops what cannot be parsed)."""
        out = []
        for r in range(self.table.rowCount()):
            row = {}
            for col, (key, _title, kind, _choices) in enumerate(self.columns):
                item = self.table.item(r, col)
                row[key] = item.checkState() == Qt.Checked if kind == "bool" else item.text()
            out.append(row)
        return out

    def entries(self):
        out = []
        for r in range(self.table.rowCount()):
            texts = {}
            for col, (key, _title, kind, _choices) in enumerate(self.columns):
                item = self.table.item(r, col)
                texts[key] = item.checkState() == Qt.Checked if kind == "bool" else item.text()
            original = self.table.item(r, 0).data(Qt.UserRole)
            if original is None and not any(texts.values()):
                continue  # a blank new row
            out.append(self.dump(original, texts))
        return out


ROW_OTHER = "Other"  # a valueFrom this editor does not know: shown, kept as it was


class SourceDelegate(ComboDelegate):
    """The Source column: every kind of variable, except a source this editor does not know."""

    def createEditor(self, parent, option, index):
        return None if index.data() == ROW_OTHER else super().createEditor(parent, option, index)


class ChoiceDelegate(QStyledItemDelegate):
    """A column whose cells get an editable drop-down (known values, anything else can be typed) on the rows for which
    `choices_for(source)` returns a list, and plain text editing on the others. `column_source` is the Source cell's column."""

    def __init__(self, choices_for, parent=None):
        super().__init__(parent)
        self.choices_for = choices_for

    def createEditor(self, parent, option, index):
        choices = self.choices_for(index.sibling(index.row(), 0).data())
        if not choices:
            return super().createEditor(parent, option, index)
        box = QComboBox(parent)
        box.setEditable(True)
        box.addItems(list(choices))
        return box

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentText(str(index.data() or ""))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
        else:
            super().setModelData(editor, model, index)


ValueDelegate = NameDelegate = ChoiceDelegate  # the Value column offers field paths / "name / " ; the Name column offers names


class EnvEditor(QWidget):
    """The environment of one container in one table. The Source column says what a row is: a plain Value, a Field of the
    pod (POD_NAMESPACE from metadata.namespace), a key of a Secret or ConfigMap, a Resource limit/request, or all keys of a
    ConfigMap or Secret (`envFrom`; Name is then the ConfigMap/Secret and Value the optional prefix). Change the Source of a
    row to change its kind. What the form does not show (apiVersion, optional, divisor, ...) is kept."""

    changed = Signal()

    def __init__(self, entries, from_entries=(), ref_names=None):
        super().__init__()
        self.ref_names = ref_names or {}  # {"Secret": directory, "ConfigMap": directory}: the names that exist (may load later)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Source", "Name", "Value / reference"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(150)
        self.table.setItemDelegateForColumn(0, SourceDelegate(ENV_SOURCES, self.table))
        self.table.setItemDelegateForColumn(1, NameDelegate(self._name_choices, self.table))
        self.table.setItemDelegateForColumn(2, ValueDelegate(self._value_choices, self.table))
        # One add row: pick what kind of variable, type its name and value, press Add (Enter does too)
        self.new_source = QComboBox()
        self.new_source.addItems(list(ENV_SOURCES))
        self.new_source.setToolTip("What the variable is: a plain value, a pod field, a Secret or ConfigMap key, a resource "
                                   "limit, or all keys of a ConfigMap/Secret")
        self.new_name = QLineEdit()
        self.name_completer = QCompleter([], self.new_name)
        self.name_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.name_completer.setFilterMode(Qt.MatchContains)
        self.new_name.setCompleter(self.name_completer)
        self.name_arrow = self.new_name.addAction(self.style().standardIcon(QStyle.SP_ArrowDown), QLineEdit.TrailingPosition)
        self.name_arrow.setToolTip("Show the names that exist in this namespace (you can type one that does not exist yet)")
        self.name_arrow.setVisible(False)
        self.name_arrow.triggered.connect(lambda: (self.name_completer.setCompletionPrefix(""), self.name_completer.complete()))
        self.new_value = QComboBox()
        self.new_value.setEditable(True)
        self.add_button = QPushButton("Add")
        self.add_button.setEnabled(False)
        self.remove_button = QPushButton("Remove selected")
        add_row = QHBoxLayout()
        add_row.addWidget(self.new_source)
        add_row.addWidget(self.new_name, 1)
        add_row.addWidget(self.new_value, 1)
        add_row.addWidget(self.add_button)
        add_row.addWidget(self.remove_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Environment variables</b>"))
        lay.addWidget(self.table)
        lay.addLayout(add_row)
        self._loading = True
        for entry in entries:
            self._append(entry)
        for entry in from_entries:
            self._append_from(entry)
        self._loading = False
        self.new_source.currentTextChanged.connect(self._new_source_changed)
        for directory in self.ref_names.values():
            directory.finished.connect(lambda: self._new_source_changed(self.new_source.currentText()))
        self.new_name.textChanged.connect(lambda text: self.add_button.setEnabled(bool(text.strip())))
        self.new_name.returnPressed.connect(self._add_typed)
        self.new_value.lineEdit().returnPressed.connect(self._add_typed)
        self.add_button.clicked.connect(self._add_typed)
        self.remove_button.clicked.connect(self._remove)
        self.table.itemChanged.connect(self._item_changed)
        self._new_source_changed(self.new_source.currentText())

    def _new_source_changed(self, source):
        """The add row's hints follow the chosen kind, and Field/Resource offer their usual values."""
        name_hint, value_hint = ENV_ADD_HINTS[source]
        self.new_name.setPlaceholderText(name_hint)
        names = self._name_choices(source)
        self.name_completer.setModel(QStringListModel(names, self.name_completer))
        self.name_arrow.setVisible(bool(names))
        self.new_value.clear()
        self.new_value.addItems(self._value_choices(source))
        self.new_value.setCurrentIndex(-1)
        self.new_value.lineEdit().setPlaceholderText(value_hint)

    def _add_typed(self):
        name = self.new_name.text().strip()
        if not name:
            return
        self.add_row(self.new_source.currentText(), name, self.new_value.currentText())
        self.new_name.clear()
        self.new_value.setCurrentIndex(-1)
        self.new_value.clearEditText()
        self.new_name.setFocus()

    def _known(self, source):
        """Names of the Secrets or ConfigMaps that exist, for a Source about one of them."""
        kind = "Secret" if source.startswith("Secret") else "ConfigMap" if source.startswith("ConfigMap") else None
        directory = self.ref_names.get(kind)
        return sorted(directory.entries) if directory is not None else []

    def _name_choices(self, source):
        """The Name cell lists existing ConfigMaps/Secrets for an all-keys row; anything else is typed."""
        return self._known(source) if source in ENV_FROM_SOURCES else []

    def _value_choices(self, source):
        """The Value cell: usual field paths and resources, or "name / " for each existing Secret/ConfigMap key row."""
        if source == "Field":
            return list(ENV_FIELD_PATHS)
        if source == "Resource":
            return list(ENV_RESOURCES)
        if source in ("Secret key", "ConfigMap key"):
            return [f"{name} / " for name in self._known(source)]
        return []

    def _item_changed(self, item):
        if self._loading:
            return
        if item.column() == 0:
            self._tip(item.row())
        self.changed.emit()

    def _tip(self, row):
        source = self.table.item(row, 0).text()
        tips = {"Field": "A field of the pod: metadata.name, metadata.namespace, spec.nodeName, status.podIP…",
                "Secret key": "The Secret and its key, as  name / key", "ConfigMap key": "The ConfigMap and its key, as  name / key",
                "Resource": "A limit or request of this container, e.g. limits.memory",
                "ConfigMap, all keys": "Optional prefix added in front of every variable name",
                "Secret, all keys": "Optional prefix added in front of every variable name"}
        self.table.item(row, 2).setToolTip(tips.get(source, ""))

    def _row(self, source, name, value, original=None, locked=False):
        row = self.table.rowCount()
        self.table.setRowCount(row + 1)
        src = QTableWidgetItem(source)
        src.setData(Qt.UserRole, deepcopy(original) if original else None)
        if locked:
            for item in (src, name, value):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 0, src)
        self.table.setItem(row, 1, name)
        self.table.setItem(row, 2, value)
        self._tip(row)
        return row

    def _append(self, entry):
        label, detail = env_ref(entry) if ("valueFrom" in entry and "value" not in entry) else ("Value", str(entry.get("value", "")))
        self._row(label, QTableWidgetItem(str(entry.get("name", ""))), QTableWidgetItem(detail), entry,
                  locked=label == ROW_OTHER)

    def _append_from(self, entry):
        row = env_from_row(entry)
        self._row(f"{row['source']}, all keys", QTableWidgetItem(row["name"]), QTableWidgetItem(row["prefix"]), entry)

    def add_row(self, source, name="", value=""):
        """A new row of that Source; without a name its name cell opens for typing."""
        self._loading = True
        row = self._row(source, QTableWidgetItem(name), QTableWidgetItem(value))
        self._loading = False
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, 1))
        if not name:
            self.table.editItem(self.table.item(row, 1))
        self.changed.emit()

    def _remove(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        self.changed.emit()

    def count(self):
        return self.table.rowCount()

    def _rows(self, wanted):
        for r in range(self.table.rowCount()):
            src = self.table.item(r, 0)
            if wanted(src.text()):
                yield src, self.table.item(r, 1), self.table.item(r, 2)

    def entries(self):
        """The `env` list: each row built from its Source (a source this editor does not know comes back as it was)."""
        out = []
        for src, name, value in self._rows(lambda s: s not in ENV_FROM_SOURCES):
            if src.text() == ROW_OTHER:
                out.append(deepcopy(src.data(Qt.UserRole)))
            else:
                out.append(build_env_entry(src.data(Qt.UserRole), src.text(), name.text(), value.text()))
        return out

    def from_entries(self):
        """The `envFrom` list: each entry keeps the fields the table does not show."""
        return [build_env_from(src.data(Qt.UserRole), {"source": "Secret" if src.text().startswith("Secret") else "ConfigMap",
                                                       "name": name.text(), "prefix": prefix.text()})
                for src, name, prefix in self._rows(lambda s: s in ENV_FROM_SOURCES)]


class LinesEdit(QPlainTextEdit):
    """One item per line (a command and its arguments)."""

    def __init__(self, items, placeholder):
        super().__init__("\n".join(str(i) for i in items))
        self.setPlaceholderText(placeholder)
        self.setFixedHeight(76)
        self.setTabChangesFocus(True)

    def lines(self):
        return [line for line in self.toPlainText().splitlines() if line.strip()]


class TriCombo(QComboBox):
    """Not set / Yes / No for an optional true-or-false field."""

    def __init__(self, value=None):
        super().__init__()
        self.addItems(["Not set", "Yes", "No"])
        self.setCurrentIndex({None: 0, True: 1, False: 2}[value])

    def value(self):
        return (None, True, False)[self.currentIndex()]


class ProbeEditor(QGroupBox):
    """One probe (liveness, readiness or startup): how it checks, and its timings."""

    changed = Signal()

    def __init__(self, title, probe):
        super().__init__(title)
        v = probe_values(probe)
        self.mode = QComboBox()
        self.mode.addItems(PROBE_MODES)
        self.mode.setCurrentText(v["mode"])
        self.path = QLineEdit(v["path"], placeholderText="/healthz")
        self.scheme = QComboBox()
        self.scheme.addItems(["", "HTTP", "HTTPS"])
        self.scheme.setCurrentText(v["scheme"] if v["scheme"] in ("HTTP", "HTTPS") else "")
        self.port = QLineEdit(v["port"], placeholderText="8080 or a port name")
        self.command = LinesEdit(v["command"], "One word per line, e.g.\ncat\n/tmp/ready")
        self.service = QLineEdit(v["service"], placeholderText="optional")
        self.timings = {key: QLineEdit(v["timings"][key], placeholderText="default") for key, _l in PROBE_TIMINGS}
        self.form = QFormLayout(self)
        self.form.addRow("Check", self.mode)
        self.form.addRow("Path", self.path)
        self.form.addRow("Scheme", self.scheme)
        self.form.addRow("Port", self.port)
        self.form.addRow("Command", self.command)
        self.form.addRow("gRPC service", self.service)
        for key, label in PROBE_TIMINGS:
            self.form.addRow(label, self.timings[key])
        self.mode.currentTextChanged.connect(self._mode_changed)
        for edit in (self.path, self.port, self.service, *self.timings.values()):
            edit.textChanged.connect(self.changed)
        self.scheme.currentIndexChanged.connect(self.changed)
        self.command.textChanged.connect(self.changed)
        self._mode_changed()

    def _mode_changed(self):
        mode = self.mode.currentText()
        show = {self.path: mode == "HTTP GET", self.scheme: mode == "HTTP GET", self.port: mode in ("HTTP GET", "TCP socket", "gRPC"),
                self.command: mode == "Command", self.service: mode == "gRPC"}
        for widget, visible in show.items():
            self.form.setRowVisible(widget, visible)
        for edit in self.timings.values():
            self.form.setRowVisible(edit, mode != "None")
        self.changed.emit()

    def values(self):
        return {"mode": self.mode.currentText(), "path": self.path.text(), "scheme": self.scheme.currentText(),
                "port": self.port.text(), "command": self.command.lines(), "service": self.service.text(),
                "timings": {key: edit.text() for key, edit in self.timings.items()}}


def scrolled(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(widget)
    return area


def page(*parts, grow=False):
    """A tab page: the widgets or layouts stacked. The spare room goes at the bottom, or (`grow`) to the table in it."""
    widget = QWidget()
    lay = QVBoxLayout(widget)
    for part in parts:
        if isinstance(part, QFormLayout):
            lay.addLayout(part)
        else:
            lay.addWidget(part)
    if not grow:
        lay.addStretch(1)
    return widget


class ContainerForm(QWidget):
    """The editable fields of one container, one sub-tab per topic. A tab's title carries a count (or a dot) when it holds
    something, so the filled-in topics can be seen without opening each."""

    changed = Signal()

    def __init__(self, field, index, container, create=False, locked=False, image_locked=False, ref_names=None):
        super().__init__()
        self.field, self.index, self.name = field, index, container.get("name", f"#{index + 1}")
        self.container_name = QLineEdit(self.name) if create else None  # a new object names its container
        self.image = QLineEdit(str(container.get("image", "")))
        self.pull = QComboBox()
        self.pull.addItems([p or "Not set" for p in PULL_POLICIES])
        current = container.get("imagePullPolicy", "")
        self.pull.setCurrentIndex(PULL_POLICIES.index(current) if current in PULL_POLICIES else 0)
        self.pull.setToolTip("Not set: Always for :latest or no tag, otherwise IfNotPresent.")
        self.command = LinesEdit(container.get("command") or [], "Replaces the image's entrypoint; one word per line")
        self.args = LinesEdit(container.get("args") or [], "Arguments; one per line")
        main = QFormLayout()
        if self.container_name is not None:
            main.addRow("Container name", self.container_name)
        main.addRow("Image", self.image)
        main.addRow("Pull policy", self.pull)
        main.addRow("Command", self.command)
        main.addRow("Arguments", self.args)

        self.boxes = {}
        resources = container.get("resources") or {}
        res_form = QFormLayout()
        for group in ("requests", "limits"):
            for res, label, hint in RESOURCES:
                box = QLineEdit(str((resources.get(group) or {}).get(res, "")))
                box.setPlaceholderText(hint)
                self.boxes[group, res] = box
                res_form.addRow(f"{label} {group[:-1]}", box)

        self.env = EnvEditor(container.get("env") or [], container.get("envFrom") or [], ref_names)
        self.ports = RowsEditor("Ports", [("name", "Name", "text", ()), ("containerPort", "Container port", "int", ()),
                                          ("protocol", "Protocol", "choice", ("", *PROTOCOLS))],
                                container.get("ports") or [], new_entry={"containerPort": 80}, add_text="Add port")
        self.mounts = RowsEditor("Volume mounts", [("name", "Volume", "text", ()), ("mountPath", "Mount path", "text", ()),
                                                   ("subPath", "Sub path", "text", ()), ("readOnly", "Read only", "bool", ())],
                                 container.get("volumeMounts") or [], add_text="Add mount",
                                 note="The volume name must be one of the pod's volumes (Pod tab).")
        self.probe_editors = {key: ProbeEditor(label, container.get(key)) for key, label in PROBE_NAMES}

        ctx = container.get("securityContext") or {}
        self.ids = {key: QLineEdit("" if ctx.get(key) is None else str(ctx[key])) for key, _l in CONTAINER_IDS}
        self.flags = {key: TriCombo(ctx.get(key)) for key, _l in CONTAINER_FLAGS}
        security = QFormLayout()
        for key, label in CONTAINER_IDS:
            self.ids[key].setPlaceholderText("not set")
            security.addRow(label, self.ids[key])
        for key, label in CONTAINER_FLAGS:
            security.addRow(label, self.flags[key])

        self.sub = QTabWidget()
        self._pages = [("Image and command", page(main)), ("Resources", page(res_form)),
                       ("Environment", page(self.env, grow=True)), ("Ports", page(self.ports, grow=True)),
                       ("Probes", scrolled(page(*self.probe_editors.values()))),
                       ("Volume mounts", page(self.mounts, grow=True)), ("Security", page(security))]
        for _title, widget in self._pages:
            self.sub.addTab(widget, _title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.sub)

        for edit in (self.image, *self.boxes.values(), *self.ids.values(), *([self.container_name] if create else [])):
            edit.textChanged.connect(self._changed)
        self.pull.currentIndexChanged.connect(self._changed)
        for lines in (self.command, self.args):
            lines.textChanged.connect(self._changed)
        for editor in (self.env, self.ports, self.mounts, *self.probe_editors.values()):
            editor.changed.connect(self._changed)
        for box in self.flags.values():
            box.currentIndexChanged.connect(self._changed)
        if locked:  # a running pod: only the image can change
            for widget in (self.pull, self.command, self.args):
                widget.setEnabled(False)
            for _title, widget in self._pages[1:]:
                widget.setEnabled(False)
        if image_locked:  # a Job's template cannot change at all
            self.image.setEnabled(False)
        self._titles()

    def _changed(self, *_args):
        self._titles()
        self.changed.emit()

    def _titles(self):
        probes = sum(1 for p in self.probe_editors.values() if p.mode.currentText() != "None")
        secure = any(e.text().strip() for e in self.ids.values()) or any(b.value() is not None for b in self.flags.values())
        resources = sum(1 for b in self.boxes.values() if b.text().strip())
        counts = {"Resources": resources, "Environment": self.env.count(),
                  "Ports": self.ports.count(), "Probes": probes, "Volume mounts": self.mounts.count(),
                  "Security": 1 if secure else 0}
        for i, (title, _w) in enumerate(self._pages):
            n = counts.get(title)
            self.sub.setTabText(i, title + (f" ({n})" if n else ""))

    def values(self):
        extra = {"container_name": self.container_name.text()} if self.container_name is not None else {}
        return {**extra, "name": self.name, "field": self.field, "index": self.index, "image": self.image.text(),
                "pull": PULL_POLICIES[self.pull.currentIndex()],
                "command": self.command.lines(), "args": self.args.lines(),
                "requests": {res: self.boxes["requests", res].text() for res, _l, _h in RESOURCES},
                "limits": {res: self.boxes["limits", res].text() for res, _l, _h in RESOURCES},
                "env": self.env.entries(), "env_from": self.env.from_entries(), "ports": self.ports.entries(),
                "mounts": self.mounts.entries(),
                "probes": {key: editor.values() for key, editor in self.probe_editors.items()},
                "security": {**{key: edit.text() for key, edit in self.ids.items()},
                             **{key: box.value() for key, box in self.flags.items()}}}


class PodForm(QWidget):
    """The pod template: identity and scheduling, volumes, pod labels and pod-level security, one sub-tab each."""

    changed = Signal()

    def __init__(self, template, is_pod=False, locked=False, jobs=False, all_locked=False):
        super().__init__()
        self.is_pod = is_pod
        self.restart = None
        pod = template.get("spec") or {}
        meta = template.get("metadata") or {}
        self.service_account = QLineEdit(str(pod.get("serviceAccountName", "")), placeholderText="default")
        self.priority = QLineEdit(str(pod.get("priorityClassName", "")), placeholderText="not set")
        self.grace = QLineEdit("" if pod.get("terminationGracePeriodSeconds") is None
                               else str(pod["terminationGracePeriodSeconds"]), placeholderText="30 (the default)")
        self.node_selector = KeyValueEditor("Node selector", height=90)
        self.node_selector.set_items(dict(pod.get("nodeSelector") or {}))
        self.tolerations = RowsEditor(
            "Tolerations", [("key", "Key", "text", ()), ("operator", "Operator", "choice", ("", *TOLERATION_OPERATORS)),
                            ("value", "Value", "text", ()), ("effect", "Effect", "choice", ("", *TAINT_EFFECTS)),
                            ("tolerationSeconds", "Seconds", "int", ())],
            pod.get("tolerations") or [], new_entry={"operator": "Exists"}, add_text="Add toleration", height=90)
        self.pull_secrets = RowsEditor("Image pull secrets", [("name", "Secret name", "text", ())],
                                       pod.get("imagePullSecrets") or [], add_text="Add secret", height=70)
        identity = QFormLayout()
        identity.addRow("Service account", self.service_account)
        identity.addRow("Priority class", self.priority)
        identity.addRow("Termination grace (s)", self.grace)
        if jobs:  # a Job's pods must not restart forever: Never or OnFailure
            self.restart = QComboBox()
            current = str(pod.get("restartPolicy", "Never"))
            self.restart.addItems([*RESTART_POLICIES, *([current] if current not in RESTART_POLICIES else [])])
            self.restart.setCurrentText(current)
            self.restart.setToolTip("Never: a failed pod is replaced by a new one. OnFailure: the container is restarted "
                                    "in the same pod.")
            identity.addRow("Restart policy", self.restart)

        self.volumes = RowsEditor(
            None, [("name", "Name", "text", ()), ("type", "Type", "choice", VOLUME_TYPES),
                   ("detail", "ConfigMap / Secret / claim / path / medium", "text", ())],
            pod.get("volumes") or [], new_entry={"name": "", "emptyDir": {}}, add_text="Add volume",
            load=volume_row, dump=build_volume, existing_readonly=("type",), height=160,
            note="The type of an existing volume cannot be changed: remove it and add another. Volumes of other types "
                 f"are listed as {OTHER_VOLUME} and kept as they are; containers mount them on their Volume mounts tab.")

        self.template_labels = KeyValueEditor("Pod labels", height=90)
        self.template_annotations = KeyValueEditor("Pod annotations", height=90)
        self.template_labels.set_items(dict(meta.get("labels") or {}))
        self.template_annotations.set_items(dict(meta.get("annotations") or {}))
        labels_note = QLabel("Pod labels must keep the labels the workload's selector looks for. Changing them starts a "
                             "rollout.")
        labels_note.setWordWrap(True)
        theme.muted(labels_note)

        ctx = pod.get("securityContext") or {}
        self.ids = {key: QLineEdit("" if ctx.get(key) is None else str(ctx[key]), placeholderText="not set")
                    for key in ("runAsUser", "runAsGroup", "fsGroup")}
        self.non_root = TriCombo(ctx.get("runAsNonRoot"))
        security = QFormLayout()
        security.addRow("Run as user ID", self.ids["runAsUser"])
        security.addRow("Run as group ID", self.ids["runAsGroup"])
        security.addRow("Volume group ID (fsGroup)", self.ids["fsGroup"])
        security.addRow("Must run as non-root", self.non_root)

        self.sub = QTabWidget()
        self._pages = [("Identity and scheduling", scrolled(page(identity, self.node_selector, self.tolerations,
                                                              self.pull_secrets))),
                       ("Volumes", page(self.volumes, grow=True)),
                       ("Pod labels", page(labels_note, self.template_labels, self.template_annotations)),
                       ("Security", page(security))]
        if is_pod:  # a Pod's labels are its own, on the first tab
            self._unused_page = self._pages.pop(2)  # kept alive: it owns the label editors
        for title, widget in self._pages:
            self.sub.addTab(widget, title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.sub)
        for edit in (self.service_account, self.priority, self.grace, *self.ids.values()):
            edit.textChanged.connect(self._changed)
        self.non_root.currentIndexChanged.connect(self._changed)
        for editor in (self.node_selector, self.tolerations, self.pull_secrets, self.volumes, self.template_labels,
                       self.template_annotations):
            editor.changed.connect(self._changed)
        if locked:  # a running pod: tolerations can only be added, nothing else here changes
            self.tolerations.lock_existing("A running pod only accepts added tolerations: the ones it has cannot be changed or "
                                           "removed.")
            for widget in (self.service_account, self.priority, self.grace, self.node_selector, self.pull_secrets,
                           self.volumes, *self.ids.values(), self.non_root, *([self.restart] if self.restart else [])):
                widget.setEnabled(False)
        if all_locked:  # a Job's template is fixed, tolerations included
            self.tolerations.setEnabled(False)
            for editor in (self.template_labels, self.template_annotations):
                editor.setEnabled(False)
        self._titles()

    def _changed(self, *_args):
        self._titles()
        self.changed.emit()

    def _titles(self):
        counts = {"Volumes": self.volumes.count(),
                  "Identity and scheduling": self.tolerations.count() + self.pull_secrets.count()
                  + len(self.node_selector.items()) + sum(1 for e in (self.service_account, self.priority, self.grace)
                                                           if e.text().strip())}
        for i, (title, _w) in enumerate(self._pages):
            n = counts.get(title)
            self.sub.setTabText(i, title + (f" ({n})" if n else ""))

    def values(self):
        return {"service_account": self.service_account.text(), "priority_class": self.priority.text(),
                "grace": self.grace.text(),
                "node_selector": self.node_selector.items(), "node_selector_pairs": self.node_selector.pairs(),
                "tolerations": self.tolerations.entries(), "pull_secrets": self.pull_secrets.entries(),
                "volumes": self.volumes.entries(),
                "template_labels": self.template_labels.items(), "template_labels_pairs": self.template_labels.pairs(),
                "template_annotations": self.template_annotations.items(),
                "template_annotations_pairs": self.template_annotations.pairs(),
                "restart_policy": self.restart.currentText() if self.restart is not None else "",
                "security": {**{key: edit.text() for key, edit in self.ids.items()}, "runAsNonRoot": self.non_root.value()}}
