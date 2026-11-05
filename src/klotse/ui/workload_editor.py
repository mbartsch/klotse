"""Form editor for a Deployment, DaemonSet or StatefulSet.

The form covers what people change most: replicas, the update strategy, and per container the image, pull policy, CPU and
memory requests/limits and plain environment variables, plus the workload's labels and annotations. Only those fields
change: everything else in the manifest is kept (environment variables that come from a Secret, ConfigMap or field are
shown read-only and written back untouched). Changing a container changes the pod template, which makes Kubernetes
roll the pods out again; the dialog says so. "Edit as YAML…" falls back to the YAML editor for everything the form
does not cover."""
import re
from copy import deepcopy
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .role_editor import confirm_risky
from .service_account_editor import ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor, validate_pairs

WORKLOAD_KINDS = ("Deployment", "DaemonSet", "StatefulSet")
HAS_REPLICAS = ("Deployment", "StatefulSet")
PULL_POLICIES = ("", "Always", "IfNotPresent", "Never")  # "" = not set (Kubernetes picks from the image tag)
# kind -> (spec field, strategy types, [(rollingUpdate key, label, kind of number)])
STRATEGIES = {
    "Deployment": ("strategy", ("RollingUpdate", "Recreate"),
                   [("maxSurge", "Max surge", "count_or_percent"), ("maxUnavailable", "Max unavailable", "count_or_percent")]),
    "DaemonSet": ("updateStrategy", ("RollingUpdate", "OnDelete"),
                  [("maxUnavailable", "Max unavailable", "count_or_percent")]),
    "StatefulSet": ("updateStrategy", ("RollingUpdate", "OnDelete"),
                    [("partition", "Partition (pods with a lower number keep the old version)", "count")]),
}
STRATEGY_TIPS = {
    "RollingUpdate": "Pods are replaced a few at a time.",
    "Recreate": "All old pods are stopped before new ones start (downtime).",
    "OnDelete": "Pods are only replaced when you delete them.",
}
QUANTITY = re.compile(r"(\d+(\.\d+)?|\.\d+)(n|u|m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?")
ENV_NAME = re.compile(r"[-._a-zA-Z][-._a-zA-Z0-9]*")
SCALE = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
         "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60}
RESOURCES = (("cpu", "CPU", "e.g. 250m or 0.5"), ("memory", "Memory", "e.g. 256Mi or 1Gi"))
ROLLOUT_NOTE = ("Changing a container (image, resources, environment) changes the pod template: Kubernetes then starts "
                "a rolling update and replaces the pods.")
REPLICAS_NOTE = "If a HorizontalPodAutoscaler manages this workload, it will set the replica count again."


# ---- pure helpers (tested without Qt) ---------------------------------------------
def valid_quantity(text):
    return QUANTITY.fullmatch(text.strip()) is not None


def quantity_value(text):
    """A CPU or memory quantity as a number (cores or bytes), or None when it is not valid."""
    match = QUANTITY.fullmatch(text.strip())
    if not match:
        return None
    return float(match.group(1)) * SCALE[match.group(3) or ""]


def valid_count(text, percent=False):
    """A whole number, or with `percent` also a percentage such as 25%."""
    return re.fullmatch(r"\d+%?" if percent else r"\d+", text.strip()) is not None


def number_or_text(text):
    """'2' -> 2 and '25%' -> '25%': how Kubernetes stores an int-or-percent."""
    text = text.strip()
    return int(text) if text.isdigit() else text


def env_source(entry):
    """What a variable that has no plain value comes from, in words."""
    ref = entry.get("valueFrom") or {}
    for key, label in (("secretKeyRef", "Secret"), ("configMapKeyRef", "ConfigMap")):
        if key in ref:
            return f"{label} {ref[key].get('name', '?')}, key {ref[key].get('key', '?')}"
    if "fieldRef" in ref:
        return f"field {ref['fieldRef'].get('fieldPath', '?')}"
    if "resourceFieldRef" in ref:
        return f"resource {ref['resourceFieldRef'].get('resource', '?')}"
    return "another source"


def container_lists(raw):
    """[(field, index, container)] for the init containers and then the regular ones."""
    spec = ((raw.get("spec") or {}).get("template") or {}).get("spec") or {}
    return [(field, i, c) for field in ("initContainers", "containers") for i, c in enumerate(spec.get(field) or [])
            if isinstance(c, dict)]


def workload_warnings(kind, raw):
    """Why changing this workload needs care."""
    out = []
    if (raw.get("metadata") or {}).get("namespace") == "kube-system":
        out.append("Workloads in kube-system usually belong to the cluster's own components (DNS, networking, the "
                   "proxy, add-ons); a wrong change can break the cluster.")
    return out


def validate(kind, values):
    """Problems with the edited values (a list of sentences; empty = fine). `values` is what `build_body` takes."""
    problems = []
    if values.get("strategy_type") == "RollingUpdate":
        for key, label, number in STRATEGIES[kind][2]:
            text = values.get("knobs", {}).get(key, "").strip()
            percent = number == "count_or_percent"
            if text and not valid_count(text, percent=percent):
                problems.append(f"{label.split(' (')[0]}: enter a whole number" + (" or a percentage such as 25%" if percent else ""))
    for field, label in (("min_ready", "Min ready seconds"), ("history", "Revisions kept")):
        text = values.get(field, "").strip()
        if text and not valid_count(text):
            problems.append(f"{label}: enter a whole number or leave it empty")
    for c in values.get("containers", []):
        name = c["name"]
        if not c["image"].strip():
            problems.append(f"{name}: enter an image")
        numbers = {}
        for group in ("requests", "limits"):
            for res, label, _hint in RESOURCES:
                text = c[group].get(res, "").strip()
                if text and not valid_quantity(text):
                    problems.append(f"{name}: {label} {group[:-1]} \"{text}\" is not a valid quantity")
                elif text:
                    numbers[group, res] = quantity_value(text)
        for res, label, _hint in RESOURCES:
            low, high = numbers.get(("requests", res)), numbers.get(("limits", res))
            if low is not None and high is not None and low > high:
                problems.append(f"{name}: the {label} request is higher than the limit")
        seen = set()
        for entry in c["env"]:
            var = entry.get("name", "")
            if not ENV_NAME.fullmatch(var):
                problems.append(f"{name}: \"{var}\" is not a valid variable name")
            elif var in seen:
                problems.append(f"{name}: the variable {var} is listed twice")
            seen.add(var)
    problems += validate_pairs(values.get("annotations_pairs", []), "annotation")
    problems += validate_pairs(values.get("labels_pairs", []), "label")
    return problems


def image_hints(values):
    """Soft advice (not errors) about the images typed."""
    out = []
    for c in values.get("containers", []):
        image = c["image"].strip()
        last = image.rsplit("/", 1)[-1]
        if image and "@" not in image and (":" not in last or last.endswith(":latest")):
            out.append(f"{c['name']}: the image has no fixed tag, so a restart can pull a different version")
    return out


def _set_or_pop(mapping, key, value):
    if value in ("", None):
        mapping.pop(key, None)
    else:
        mapping[key] = value


def build_body(kind, raw, values):
    """The manifest with the edited fields; everything else is copied. `values`: replicas (int), strategy_type,
    knobs {key: text}, min_ready, history (text), containers [{name, field, index, image, pull, requests, limits, env}],
    labels, annotations (dicts)."""
    body = deepcopy(raw)
    spec = body.setdefault("spec", {})
    if kind in HAS_REPLICAS and values.get("replicas") is not None:
        spec["replicas"] = int(values["replicas"])
    _set_or_pop(spec, "minReadySeconds", int(values["min_ready"]) if values.get("min_ready", "").strip() else None)
    _set_or_pop(spec, "revisionHistoryLimit", int(values["history"]) if values.get("history", "").strip() else None)
    field, _types, knobs = STRATEGIES[kind]
    strategy = spec.get(field) or {}
    strategy["type"] = values["strategy_type"]
    if values["strategy_type"] == "RollingUpdate":
        rolling = strategy.get("rollingUpdate") or {}
        for key, _label, _number in knobs:
            text = values.get("knobs", {}).get(key, "").strip()
            _set_or_pop(rolling, key, number_or_text(text) if text else None)
        _set_or_pop(strategy, "rollingUpdate", rolling or None)
    else:
        strategy.pop("rollingUpdate", None)
    spec[field] = strategy
    pod = spec.setdefault("template", {}).setdefault("spec", {})
    for c in values.get("containers", []):
        target = pod[c["field"]][c["index"]]
        target["image"] = c["image"].strip()
        _set_or_pop(target, "imagePullPolicy", c["pull"])
        resources = target.get("resources") or {}
        for group in ("requests", "limits"):
            current = resources.get(group) or {}
            for res, _label, _hint in RESOURCES:
                _set_or_pop(current, res, c[group].get(res, "").strip())
            _set_or_pop(resources, group, current or None)
        _set_or_pop(target, "resources", resources or None)
        _set_or_pop(target, "env", [deepcopy(e) for e in c["env"]] or None)
    meta = body.setdefault("metadata", {})
    for key in ("labels", "annotations"):
        _set_or_pop(meta, key, dict(values.get(key) or {}) or None)
    return body


def template_changed(raw, body):
    return (raw.get("spec") or {}).get("template") != (body.get("spec") or {}).get("template")


# ---- widgets ----------------------------------------------------------------------
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

    def entries(self):
        out = []
        for r in range(self.table.rowCount()):
            name, value = self.table.item(r, 0), self.table.item(r, 1)
            kept = name.data(Qt.UserRole)
            out.append(deepcopy(kept) if kept else {"name": name.text().strip(), "value": value.text() if value else ""})
        return out


class ContainerForm(QWidget):
    """The editable fields of one container."""

    changed = Signal()

    def __init__(self, field, index, container):
        super().__init__()
        self.field, self.index, self.name = field, index, container.get("name", f"#{index + 1}")
        self.image = QLineEdit(str(container.get("image", "")))
        self.pull = QComboBox()
        self.pull.addItems([p or "Not set" for p in PULL_POLICIES])
        current = container.get("imagePullPolicy", "")
        self.pull.setCurrentIndex(PULL_POLICIES.index(current) if current in PULL_POLICIES else 0)
        self.pull.setToolTip("Not set: Always for :latest or no tag, otherwise IfNotPresent.")
        self.boxes = {}
        resources = container.get("resources") or {}
        form = QFormLayout()
        form.addRow("Image", self.image)
        form.addRow("Pull policy", self.pull)
        for group in ("requests", "limits"):
            for res, label, hint in RESOURCES:
                box = QLineEdit(str((resources.get(group) or {}).get(res, "")))
                box.setPlaceholderText(hint)
                self.boxes[group, res] = box
                form.addRow(f"{label} {group[:-1]}", box)
        self.env = EnvEditor(container.get("env") or [])
        for edit in (self.image, *self.boxes.values()):
            edit.textChanged.connect(self.changed)
        self.pull.currentIndexChanged.connect(self.changed)
        self.env.changed.connect(self.changed)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.env)

    def values(self):
        return {"name": self.name, "field": self.field, "index": self.index, "image": self.image.text(),
                "pull": PULL_POLICIES[self.pull.currentIndex()],
                "requests": {res: self.boxes["requests", res].text() for res, _l, _h in RESOURCES},
                "limits": {res: self.boxes["limits", res].text() for res, _l, _h in RESOURCES},
                "env": self.env.entries()}


class WorkloadEditDialog(QDialog):
    """Edit a Deployment, DaemonSet or StatefulSet. After Save, `result_body` is the new manifest; "Edit as YAML…" sets
    `wants_yaml` and rejects the dialog."""

    def __init__(self, kind, raw, parent=None):
        super().__init__(parent)
        self.kind, self._raw = kind, raw
        self.result_body, self.wants_yaml = None, False
        meta, spec = raw.get("metadata") or {}, raw.get("spec") or {}
        self.warnings = workload_warnings(kind, raw)
        self.setWindowTitle(f"Edit {kind} {meta.get('name', '')}")
        self.resize(820, 760)
        self.head = QLabel(f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                           f"<b>{escape(str(meta.get('namespace', '')))}</b>. Only the fields below change; the rest of "
                           "the manifest is kept. " + escape(ROLLOUT_NOTE))
        self.head.setWordWrap(True)
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(w) for w in self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(self.warnings))

        general = QFormLayout()
        self.replicas = QSpinBox()
        self.replicas.setRange(0, 10000)
        self.replicas.setValue(int(spec.get("replicas", 1)))
        if kind in HAS_REPLICAS:
            self.replicas.setToolTip(REPLICAS_NOTE)
            general.addRow("Replicas", self.replicas)
        field, types, knobs = STRATEGIES[kind]
        strategy = spec.get(field) or {}
        self.strategy = QComboBox()
        self.strategy.addItems(types)
        self.strategy.setCurrentIndex(types.index(strategy["type"]) if strategy.get("type") in types else 0)
        general.addRow("Update strategy", self.strategy)
        self.strategy_note = QLabel()
        self.strategy_note.setWordWrap(True)
        theme.muted(self.strategy_note)
        general.addRow("", self.strategy_note)
        self.knobs = {}
        rolling = strategy.get("rollingUpdate") or {}
        for key, label, _number in knobs:
            edit = QLineEdit("" if rolling.get(key) is None else str(rolling[key]))
            edit.setPlaceholderText("not set")
            self.knobs[key] = edit
            general.addRow(label, edit)
        self.min_ready = QLineEdit("" if spec.get("minReadySeconds") is None else str(spec["minReadySeconds"]))
        self.history = QLineEdit("" if spec.get("revisionHistoryLimit") is None else str(spec["revisionHistoryLimit"]))
        for edit in (self.min_ready, self.history):
            edit.setPlaceholderText("not set")
        general.addRow("Min ready seconds", self.min_ready)
        general.addRow("Revisions kept", self.history)

        self.tabs = QTabWidget()
        self.forms = []
        for field_name, index, container in container_lists(raw):
            form_widget = ContainerForm(field_name, index, container)
            self.forms.append(form_widget)
            form_widget.changed.connect(self._hint)
            self.tabs.addTab(form_widget, form_widget.name + (" (init)" if field_name == "initContainers" else ""))

        self.annotations = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,))
        self.labels = KeyValueEditor("Labels")
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.addLayout(general)
        lay.addWidget(QLabel("<b>Containers</b>"))
        lay.addWidget(self.tabs)
        lay.addWidget(self.annotations)
        lay.addWidget(self.labels)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.yaml_button = QPushButton("Edit as YAML…")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.yaml_button)
        bottom.addWidget(buttons)
        outer = QVBoxLayout(self)
        outer.addWidget(self.head)
        outer.addWidget(self.banner)
        outer.addWidget(scroll, 1)
        outer.addLayout(bottom)

        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.yaml_button.clicked.connect(self._as_yaml)
        for edit in (*self.knobs.values(), self.min_ready, self.history):
            edit.textChanged.connect(self._hint)
        for editor in (self.annotations, self.labels):
            editor.changed.connect(self._hint)
        self._strategy_changed()

    # ---- state ----
    def values(self):
        return {"replicas": self.replicas.value(), "strategy_type": self.strategy.currentText(),
                "knobs": {key: edit.text() for key, edit in self.knobs.items()},
                "min_ready": self.min_ready.text(), "history": self.history.text(),
                "containers": [f.values() for f in self.forms],
                "labels": self.labels.items(), "annotations": self.annotations.items(),
                "labels_pairs": self.labels.pairs(), "annotations_pairs": self.annotations.pairs()}

    def _strategy_changed(self):
        kind = self.strategy.currentText()
        self.strategy_note.setText(STRATEGY_TIPS.get(kind, ""))
        for edit in self.knobs.values():
            edit.setEnabled(kind == "RollingUpdate")
        self._hint()

    def _hint(self):
        values = self.values()
        problems = validate(self.kind, values)
        if problems:
            self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(problems[0])}</span>")
            return
        soft = image_hints(values)
        self.hint.setText(escape(soft[0]) if soft else "")

    def _as_yaml(self):
        self.wants_yaml = True
        self.reject()

    def _save(self):
        values = self.values()
        problems = validate(self.kind, values)
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        name = (self._raw.get("metadata") or {}).get("name", "")
        if not confirm_risky(self, self.kind, name, self.warnings):
            return
        self.result_body = build_body(self.kind, self._raw, values)
        self.accept()
