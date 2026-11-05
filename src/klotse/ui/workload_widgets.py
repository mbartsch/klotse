"""Widgets of the workload editor: an editable rows table, probes, container and pod forms. The data rules live in
workload_model.py; these classes only show the manifest's values and give them back (`values()`)."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QStyledItemDelegate, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .service_account_editor import KeyValueEditor
from .workload_model import (CONTAINER_FLAGS, CONTAINER_IDS, OTHER_VOLUME, RESTART_POLICIES, PROBE_MODES, PROBE_NAMES, PROBE_TIMINGS,
                             PROTOCOLS, PULL_POLICIES, RESOURCES, TAINT_EFFECTS, TOLERATION_OPERATORS, VOLUME_TYPES,
                             build_env_from, build_volume, env_from_row, env_source, probe_values,
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


class EnvEditor(QWidget):
    """Environment variables of one container. A variable that comes from a Secret, ConfigMap or field is listed read-only
    and kept exactly as it was."""

    changed = Signal()

    def __init__(self, entries):
        super().__init__()
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Value"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(110)
        self.add_button = QPushButton("Add variable")
        self.remove_button = QPushButton("Remove selected")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Environment variables</b>"))
        lay.addWidget(self.table)
        lay.addLayout(buttons)
        for entry in entries:
            self._append(entry)
        self.add_button.clicked.connect(self._add_blank)
        self.remove_button.clicked.connect(self._remove)
        self.table.itemChanged.connect(lambda _item: self.changed.emit())

    def _append(self, entry):
        row = self.table.rowCount()
        self.table.setRowCount(row + 1)
        name = QTableWidgetItem(str(entry.get("name", "")))
        if "value" in entry or "valueFrom" not in entry:
            value = QTableWidgetItem(str(entry.get("value", "")))
        else:  # not a plain value: shown, never edited
            value = QTableWidgetItem(f"(from {env_source(entry)})")
            value.setFlags(value.flags() & ~Qt.ItemIsEditable)
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            name.setData(Qt.UserRole, deepcopy(entry))
        self.table.setItem(row, 0, name)
        self.table.setItem(row, 1, value)

    def _add_blank(self):
        self._append({"name": "", "value": ""})
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _remove(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        self.changed.emit()

    def count(self):
        return self.table.rowCount()

    def entries(self):
        out = []
        for r in range(self.table.rowCount()):
            name, value = self.table.item(r, 0), self.table.item(r, 1)
            kept = name.data(Qt.UserRole)
            out.append(deepcopy(kept) if kept else {"name": name.text().strip(), "value": value.text() if value else ""})
        return out


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

    def __init__(self, field, index, container, create=False, locked=False, image_locked=False):
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

        self.env = EnvEditor(container.get("env") or [])
        self.env_from = RowsEditor(
            "Environment from", [("source", "From", "choice", ("ConfigMap", "Secret")), ("name", "Name", "text", ()),
                                 ("prefix", "Prefix (optional)", "text", ())],
            container.get("envFrom") or [], new_entry={"configMapRef": {"name": ""}}, add_text="Add source",
            load=env_from_row, dump=build_env_from, height=80,
            note="Adds every key of a ConfigMap or Secret as a variable.")
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
                       ("Environment", page(self.env, self.env_from)), ("Ports", page(self.ports, grow=True)),
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
        for editor in (self.env, self.env_from, self.ports, self.mounts, *self.probe_editors.values()):
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
        counts = {"Resources": resources, "Environment": self.env.count() + self.env_from.count(),
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
                "env": self.env.entries(), "env_from": self.env_from.entries(), "ports": self.ports.entries(),
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
