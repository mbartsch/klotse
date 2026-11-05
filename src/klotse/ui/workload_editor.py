"""Form editor for a Deployment, DaemonSet or StatefulSet.

Three sections, each with sub-tabs so that nothing is a wall of fields:

* **Workload**: replicas, update strategy, history, labels and annotations of the workload itself.
* **Pod**: identity and scheduling (service account, node selector, tolerations, pull secrets), volumes, pod labels and
  pod-level security.
* **Containers**: one tab per container (init containers too), each with Image & command, Resources, Environment, Ports,
  Probes, Volume mounts and Security.

Only the fields in the form change: everything else in the manifest is copied (environment variables from a Secret,
ConfigMap or field are shown read-only and written back untouched). Changing the pod template makes Kubernetes roll the
pods out again; the dialog says so. "Edit as YAML…" is the way to reach anything not in the form (affinity, lifecycle
hooks, volume claim templates...). The data rules are in workload_model.py, the widgets in workload_widgets.py."""
from html import escape

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout)

from . import theme
from .role_editor import confirm_risky
from .service_account_editor import ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor
from .workload_model import (HAS_REPLICAS, STRATEGIES, WORKLOAD_KINDS, build_body, container_lists, env_source,  # noqa: F401
                             image_hints, number_or_text, quantity_value, template_changed, valid_count,
                             valid_quantity, validate, workload_warnings)
from .workload_widgets import ContainerForm, PodForm, TriCombo, page, scrolled

STRATEGY_TIPS = {
    "RollingUpdate": "Pods are replaced a few at a time.",
    "Recreate": "All old pods are stopped before new ones start (downtime).",
    "OnDelete": "Pods are only replaced when you delete them.",
}
ROLLOUT_NOTE = ("Changing the pod template (containers, pod labels, volumes, scheduling) makes Kubernetes start a rolling "
                "update and replace the pods.")
REPLICAS_NOTE = "If a HorizontalPodAutoscaler manages this workload, it will set the replica count again."


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
        self.resize(900, 780)
        self.head = QLabel(f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                           f"<b>{escape(str(meta.get('namespace', '')))}</b>. Only the fields in this form change; the "
                           "rest of the manifest is kept. " + escape(ROLLOUT_NOTE))
        self.head.setWordWrap(True)
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(w) for w in self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(self.warnings))

        # ---- Workload
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
        self.progress = QLineEdit("" if spec.get("progressDeadlineSeconds") is None else str(spec["progressDeadlineSeconds"]))
        self.paused = TriCombo(spec.get("paused"))
        for edit in (self.min_ready, self.history, self.progress):
            edit.setPlaceholderText("not set")
        general.addRow("Min ready seconds", self.min_ready)
        general.addRow("Revisions kept", self.history)
        if kind == "Deployment":
            general.addRow("Progress deadline (s)", self.progress)
            general.addRow("Paused", self.paused)
        self.annotations = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,), height=90)
        self.labels = KeyValueEditor("Labels", height=90)
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))
        labels_note = QLabel("These are the workload's own labels and annotations. The pod's are on the Pod tab.")
        labels_note.setWordWrap(True)
        theme.muted(labels_note)
        workload_page = scrolled(page(general, self.labels, self.annotations, labels_note))

        # ---- Pod
        self.pod = PodForm(spec.get("template") or {})

        # ---- Containers
        self.tabs = QTabWidget()
        self.forms = []
        for field_name, index, container in container_lists(raw):
            form_widget = ContainerForm(field_name, index, container)
            self.forms.append(form_widget)
            form_widget.changed.connect(self._hint)
            self.tabs.addTab(form_widget, form_widget.name + (" (init)" if field_name == "initContainers" else ""))

        self.sections = QTabWidget()
        self.sections.addTab(workload_page, "Workload")
        self.sections.addTab(self.pod, "Pod")
        self.sections.addTab(self.tabs, "Containers")

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
        outer.addWidget(self.sections, 1)
        outer.addLayout(bottom)

        self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.yaml_button.clicked.connect(self._as_yaml)
        for edit in (*self.knobs.values(), self.min_ready, self.history, self.progress):
            edit.textChanged.connect(self._hint)
        self.paused.currentIndexChanged.connect(self._hint)
        for editor in (self.annotations, self.labels, self.pod):
            editor.changed.connect(self._hint)
        self._strategy_changed()

    # ---- state ----
    def values(self):
        return {"replicas": self.replicas.value(), "strategy_type": self.strategy.currentText(),
                "knobs": {key: edit.text() for key, edit in self.knobs.items()},
                "min_ready": self.min_ready.text(), "history": self.history.text(),
                "progress_deadline": self.progress.text(), "paused": self.paused.value(),
                "pod": self.pod.values(), "containers": [f.values() for f in self.forms],
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
        problems = validate(self.kind, values, self._raw)
        if problems:
            self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(problems[0])}</span>"
                              + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""))
            return
        soft = image_hints(values)
        self.hint.setText(escape(soft[0]) if soft else "")

    def _as_yaml(self):
        self.wants_yaml = True
        self.reject()

    def _save(self):
        values = self.values()
        problems = validate(self.kind, values, self._raw)
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        name = (self._raw.get("metadata") or {}).get("name", "")
        if not confirm_risky(self, self.kind, name, self.warnings):
            return
        self.result_body = build_body(self.kind, self._raw, values)
        self.accept()
