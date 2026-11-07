"""Form editor for a Deployment, DaemonSet, StatefulSet or Pod, to change an existing object or to create a new one.

Three sections, each with sub-tabs so that nothing is a wall of fields:

* **Workload** (**Pod** for a pod): replicas, update strategy, history, labels and annotations of the object itself.
* **Pod** (**Pod template** for a workload): identity and scheduling (service account, node selector, tolerations, pull
  secrets), volumes, pod labels and pod-level security.
* **Containers**: one tab per container (init containers too), each with Image and command, Resources, Environment,
  Ports, Probes, Volume mounts and Security.

Only the fields in the form change: everything else in the manifest is copied (environment variables from a Secret,
ConfigMap or field are shown read-only and written back untouched). Changing the pod template makes Kubernetes roll the
pods out again; the dialog says so. A running Pod can hardly be changed at all (Kubernetes accepts new container images,
`activeDeadlineSeconds`, added tolerations, labels and annotations): the other fields are shown but disabled.

"Edit as YAML…" hands the form's current values over (`pending`), so the YAML editor shows the pending changes; the YAML
editor's "Edit in form…" does the same the other way round. `create=True` starts from `workload_model.skeleton()` and adds
the name and namespace (and a StatefulSet's service name); the selector and the `app` label follow the name.
The data rules are in workload_model.py, the widgets in workload_widgets.py."""
from html import escape

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout)

from . import theme
from .edit_dialog import leave_for_yaml
from .role_editor import confirm_risky
from .service_account_editor import ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor
from .workload_model import (COMPLETION_MODES, CONCURRENCY, CREATABLE_KINDS, EDIT_KINDS, HAS_REPLICAS,  # noqa: F401
                             JOB_KINDS, STRATEGIES, WORKLOAD_KINDS, build_body, container_lists, env_source, image_hints,
                             job_spec_of, number_or_text, quantity_value, skeleton, template_changed, template_of,
                             valid_count, valid_quantity, validate, workload_warnings)
from .workload_widgets import ContainerForm, PodForm, TriCombo, page, scrolled

STRATEGY_TIPS = {
    "RollingUpdate": "Pods are replaced a few at a time.",
    "Recreate": "All old pods are stopped before new ones start (downtime).",
    "OnDelete": "Pods are only replaced when you delete them.",
}
ROLLOUT_NOTE = ("Changing the pod template (containers, pod labels, volumes, scheduling) makes Kubernetes start a rolling "
                "update and replace the pods.")
REPLICAS_NOTE = "If a HorizontalPodAutoscaler manages this workload, it will set the replica count again."
RUNNING_JOB_NOTE = ("A Job's pod template cannot be changed once it exists (it is shown but disabled). What can change: "
                    "parallelism, suspending it, the active deadline and how long it is kept after it finishes. To run "
                    "something different, create a new Job.")
CRONJOB_NOTE = "Changes apply to the Jobs it creates from now on; Jobs it already created are not touched."
RUNNING_POD_NOTE = ("A running pod can only change these: container images, the active deadline, added tolerations, "
                    "labels and annotations. Everything else is shown but cannot be edited (Kubernetes would refuse it). "
                    "To change anything else, edit the Deployment or StatefulSet that owns the pod, or delete the pod.")
NEW_NOTE = ("Fill in the name and the containers. For workloads the selector and the pod label app follow the name. "
            "Fields not in this form can be added with Edit as YAML… (the changes made here come along).")


class WorkloadEditDialog(QDialog):
    """Edit or create a Deployment, DaemonSet, StatefulSet or Pod. After Save, `result_body` is the manifest; "Edit as
    YAML…" sets `wants_yaml` (with the form's manifest in `pending`) and rejects the dialog."""

    def __init__(self, kind, raw, parent=None, create=False, namespaces=(), ref_names=None):
        super().__init__(parent)
        self.kind, self._raw, self.create = kind, raw, create
        self.running = kind in ("Pod", "Job") and not create  # these exist already: almost nothing can change
        self.result_body, self.wants_yaml, self.pending = None, False, None
        is_pod = kind == "Pod"
        meta, spec = raw.get("metadata") or {}, raw.get("spec") or {}
        self.warnings = [] if create else workload_warnings(kind, raw)
        self.setWindowTitle(f"New {kind}" if create else f"Edit {kind} {meta.get('name', '')}")
        self.resize(900, 780)
        if create:
            intro = f"New {escape(kind)}. " + escape(NEW_NOTE)
        else:
            intro = (f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                     f"<b>{escape(str(meta.get('namespace', '')))}</b>. Only the fields in this form change; the rest "
                     "of the manifest is kept. " + escape({"Pod": RUNNING_POD_NOTE, "Job": RUNNING_JOB_NOTE,
                                                           "CronJob": CRONJOB_NOTE}.get(kind, ROLLOUT_NOTE)))
        self.head = QLabel(intro)
        self.head.setWordWrap(True)
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(escape(w) for w in self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(self.warnings))

        # ---- Workload / Pod
        general = QFormLayout()
        self.name_edit = self.namespace_edit = self.service_edit = None
        if create:
            self.name_edit = QLineEdit(str(meta.get("name", "")), placeholderText="lowercase letters, digits and -")
            self.namespace_edit = QComboBox()
            self.namespace_edit.setEditable(True)
            self.namespace_edit.addItems(list(namespaces))
            self.namespace_edit.setCurrentText(str(meta.get("namespace", "")))
            general.addRow("Name", self.name_edit)
            general.addRow("Namespace", self.namespace_edit)
            if kind == "StatefulSet":
                self.service_edit = QLineEdit(placeholderText="the headless Service; empty = the name")
                general.addRow("Service name", self.service_edit)
        self.replicas = QSpinBox()
        self.replicas.setRange(0, 10000)
        self.replicas.setValue(int(spec.get("replicas", 1)))
        if kind in HAS_REPLICAS:
            self.replicas.setToolTip(REPLICAS_NOTE)
            general.addRow("Replicas", self.replicas)
        self.strategy = None
        self.strategy_note = QLabel()
        self.knobs = {}
        self.min_ready = QLineEdit("" if spec.get("minReadySeconds") is None else str(spec["minReadySeconds"]))
        self.history = QLineEdit("" if spec.get("revisionHistoryLimit") is None else str(spec["revisionHistoryLimit"]))
        self.progress = QLineEdit("" if spec.get("progressDeadlineSeconds") is None else str(spec["progressDeadlineSeconds"]))
        self.paused = TriCombo(spec.get("paused"))
        self.deadline = QLineEdit("" if spec.get("activeDeadlineSeconds") is None else str(spec["activeDeadlineSeconds"]))
        for edit in (self.min_ready, self.history, self.progress, self.deadline):
            edit.setPlaceholderText("not set")
        # Jobs and CronJobs
        job_spec = job_spec_of(raw, kind)
        self.job_edits, self.cron_edits = {}, {}
        self.completion_mode = QComboBox()
        self.completion_mode.addItems(["Not set (NonIndexed)" if m == "" else m for m in COMPLETION_MODES])
        self.completion_mode.setCurrentIndex(COMPLETION_MODES.index(job_spec["completionMode"])
                                             if job_spec.get("completionMode") in COMPLETION_MODES else 0)
        self.suspend = TriCombo(spec.get("suspend"))
        self.schedule = QLineEdit(str(spec.get("schedule", "")), placeholderText="*/5 * * * *  or  @hourly")
        self.time_zone = QLineEdit(str(spec.get("timeZone", "")), placeholderText="the controller's (usually UTC)")
        self.concurrency = QComboBox()
        self.concurrency.addItems(CONCURRENCY)
        self.concurrency.setCurrentText(spec.get("concurrencyPolicy", "Allow") if spec.get("concurrencyPolicy") in CONCURRENCY
                                        else "Allow")
        for key, label in (("parallelism", "parallelism"), ("completions", "completions"), ("backoff", "backoffLimit"),
                           ("active_deadline", "activeDeadlineSeconds"), ("ttl", "ttlSecondsAfterFinished")):
            value = job_spec.get(label)
            self.job_edits[key] = QLineEdit("" if value is None else str(value), placeholderText="not set")
        for key, label in (("starting_deadline", "startingDeadlineSeconds"), ("success_history", "successfulJobsHistoryLimit"),
                           ("failure_history", "failedJobsHistoryLimit")):
            value = spec.get(label)
            self.cron_edits[key] = QLineEdit("" if value is None else str(value), placeholderText="not set")
        self.schedule_note = QLabel("Minute, hour, day of month, month, day of week. For example 0 3 * * * is every day at "
                                    "03:00.")
        self.schedule_note.setWordWrap(True)
        theme.muted(self.schedule_note)
        if kind in STRATEGIES:
            field, types, knobs = STRATEGIES[kind]
            strategy = spec.get(field) or {}
            self.strategy = QComboBox()
            self.strategy.addItems(types)
            self.strategy.setCurrentIndex(types.index(strategy["type"]) if strategy.get("type") in types else 0)
            general.addRow("Update strategy", self.strategy)
            self.strategy_note.setWordWrap(True)
            theme.muted(self.strategy_note)
            general.addRow("", self.strategy_note)
            rolling = strategy.get("rollingUpdate") or {}
            for key, label, _number in knobs:
                edit = QLineEdit("" if rolling.get(key) is None else str(rolling[key]))
                edit.setPlaceholderText("not set")
                self.knobs[key] = edit
                general.addRow(label, edit)
            general.addRow("Min ready seconds", self.min_ready)
            general.addRow("Revisions kept", self.history)
            if kind == "Deployment":
                general.addRow("Progress deadline (s)", self.progress)
                general.addRow("Paused", self.paused)
        elif kind == "Pod":
            general.addRow("Active deadline (s)", self.deadline)
        else:  # Job, CronJob
            if kind == "CronJob":
                general.addRow("Schedule", self.schedule)
                general.addRow("", self.schedule_note)
                general.addRow("Time zone", self.time_zone)
                general.addRow("If one is still running", self.concurrency)
                general.addRow("Starting deadline (s)", self.cron_edits["starting_deadline"])
                general.addRow("Successful jobs kept", self.cron_edits["success_history"])
                general.addRow("Failed jobs kept", self.cron_edits["failure_history"])
            general.addRow("Suspended", self.suspend)
            general.addRow("Parallelism", self.job_edits["parallelism"])
            general.addRow("Completions", self.job_edits["completions"])
            general.addRow("Backoff limit (retries)", self.job_edits["backoff"])
            general.addRow("Active deadline (s)", self.job_edits["active_deadline"])
            general.addRow("Delete after finished (s)", self.job_edits["ttl"])
            general.addRow("Completion mode", self.completion_mode)
        self.annotations = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,), height=90)
        self.labels = KeyValueEditor("Labels", height=90)
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))
        labels_note = QLabel("These are the object's own labels and annotations." if is_pod
                             else "These are the workload's own labels and annotations. The pod's are on the Pod tab.")
        labels_note.setWordWrap(True)
        theme.muted(labels_note)
        workload_page = scrolled(page(general, self.labels, self.annotations, labels_note))

        # ---- the pod (a Pod's own spec, or a workload's template)
        template = {"spec": spec, "metadata": {}} if is_pod else template_of(raw, kind)
        self.pod = PodForm(template, is_pod=is_pod, locked=self.running, jobs=kind in JOB_KINDS,
                           all_locked=kind == "Job" and not create)

        # ---- containers
        self.tabs = QTabWidget()
        self.forms = []
        for field_name, index, container in container_lists(raw, kind):
            form_widget = ContainerForm(field_name, index, container, create=create, locked=self.running,
                                        image_locked=kind == "Job" and not create, ref_names=ref_names)
            self.forms.append(form_widget)
            form_widget.changed.connect(self._hint)
            self.tabs.addTab(form_widget, form_widget.name + (" (init)" if field_name == "initContainers" else ""))

        self.sections = QTabWidget()
        self.sections.addTab(workload_page, "Pod" if is_pod else "Workload")
        self.sections.addTab(self.pod, "Pod settings" if is_pod else "Pod template")
        self.sections.addTab(self.tabs, "Containers")

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.yaml_button = QPushButton("Edit as YAML…")
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

        if self.strategy is not None:
            self.strategy.currentIndexChanged.connect(self._strategy_changed)
        self.yaml_button.clicked.connect(self._as_yaml)
        typed = [*self.knobs.values(), self.min_ready, self.history, self.progress, self.deadline, self.schedule,
                 self.time_zone, *self.job_edits.values(), *self.cron_edits.values()]
        typed += [e for e in (self.name_edit, self.service_edit) if e is not None]
        for edit in typed:
            edit.textChanged.connect(self._hint)
        if self.namespace_edit is not None:
            self.namespace_edit.currentTextChanged.connect(self._hint)
        self.paused.currentIndexChanged.connect(self._hint)
        for box in (self.suspend, self.completion_mode, self.concurrency):
            box.currentIndexChanged.connect(self._hint)
        for editor in (self.annotations, self.labels, self.pod):
            editor.changed.connect(self._hint)
        self._strategy_changed()

    # ---- state ----
    def values(self):
        values = {"replicas": self.replicas.value(), "knobs": {key: edit.text() for key, edit in self.knobs.items()},
                  "min_ready": self.min_ready.text(), "history": self.history.text(),
                  "progress_deadline": self.progress.text(), "paused": self.paused.value(), "deadline": self.deadline.text(),
                  "pod": self.pod.values(), "containers": [f.values() for f in self.forms],
                  "labels": self.labels.items(), "annotations": self.annotations.items(),
                  "labels_pairs": self.labels.pairs(), "annotations_pairs": self.annotations.pairs()}
        if self.strategy is not None:
            values["strategy_type"] = self.strategy.currentText()
        if self.kind in JOB_KINDS:
            values["job"] = {**{key: edit.text() for key, edit in self.job_edits.items()},
                             "completion_mode": COMPLETION_MODES[self.completion_mode.currentIndex()],
                             "suspend": self.suspend.value()}
        if self.kind == "CronJob":
            values["cron"] = {"schedule": self.schedule.text(), "time_zone": self.time_zone.text(),
                              "concurrency": self.concurrency.currentText(),
                              **{key: edit.text() for key, edit in self.cron_edits.items()}}
        if self.create:
            values["new"] = {"name": self.name_edit.text(), "namespace": self.namespace_edit.currentText(),
                             "service_name": self.service_edit.text() if self.service_edit is not None else ""}
        return values

    def _check_raw(self):
        """The manifest the pod labels are checked against: a new object's selector follows its name, so it is not checked."""
        if not self.create:
            return self._raw
        return {**self._raw, "spec": {k: v for k, v in (self._raw.get("spec") or {}).items() if k != "selector"}}

    def pending_body(self):
        """The manifest for the form as it is now, valid or not (the YAML editor shows it as it is); None when a typed value
        cannot even be turned into one."""
        try:
            return build_body(self.kind, self._raw, self.values(), running=self.running)
        except (ValueError, KeyError, IndexError, TypeError):
            return None

    def _strategy_changed(self):
        if self.strategy is not None:
            kind = self.strategy.currentText()
            self.strategy_note.setText(STRATEGY_TIPS.get(kind, ""))
            for edit in self.knobs.values():
                edit.setEnabled(kind == "RollingUpdate")
        self._hint()

    def _hint(self):
        values = self.values()
        problems = validate(self.kind, values, self._check_raw())
        if problems:
            self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(problems[0])}</span>"
                              + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""))
            return
        soft = image_hints(values)
        self.hint.setText(escape(soft[0]) if soft else "")

    def _as_yaml(self):
        leave_for_yaml(self)

    def _save(self):
        values = self.values()
        problems = validate(self.kind, values, self._check_raw())
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        name = (self._raw.get("metadata") or {}).get("name", "")
        if not confirm_risky(self, self.kind, name, self.warnings):
            return
        self.result_body = build_body(self.kind, self._raw, values, running=self.running)
        self.accept()
