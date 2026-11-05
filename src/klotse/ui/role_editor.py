"""Form editor for the rules of a Role or ClusterRole.

API groups and resources are picked from the cluster's discovery index (ApiIndex), through a search box that shows at
most RESULT_LIMIT matches at a time, so a cluster with a huge number of custom resource types stays usable; resource
names and URLs are free text; verbs are check boxes. Only `rules` is edited: every other field of the manifest is kept."""
from copy import deepcopy

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
                               QSplitter, QToolButton, QToolTip, QVBoxLayout, QWidget)
from . import theme
from .edit_dialog import leave_for_yaml

RESULT_LIMIT = 200
SEARCH_DELAY_MS = 150
STANDARD_VERBS = ("get", "list", "watch", "create", "update", "patch", "delete", "deletecollection")
SPECIAL_VERBS = ("escalate", "bind", "impersonate", "use", "approve", "sign")
ALL = "*"
RULE_KINDS = ("Role", "ClusterRole")
FIELDS = ("apiGroups", "resources", "resourceNames", "nonResourceURLs", "verbs")


# ---- pure helpers (tested without Qt) ---------------------------------------------
def normalize_rule(rule):
    """Only the known fields, each a list of non-empty strings without duplicates (order kept); empty ones dropped.
    The core API group "" is kept for apiGroups."""
    out = {}
    for field in FIELDS:
        seen = []
        for v in (rule or {}).get(field) or []:
            v = str(v) if field == "apiGroups" else str(v).strip()
            if (v or field == "apiGroups") and v not in seen:
                seen.append(v)
        if seen:
            out[field] = seen
    return out


def is_url_rule(rule):
    return bool(rule.get("nonResourceURLs")) and not rule.get("resources")


def validate_rules(rules, cluster_scoped):
    """Problems that the API server would reject, as readable lines (empty = fine)."""
    problems = []
    for n, rule in enumerate(rules, 1):
        r = normalize_rule(rule)
        where = f"Rule {n}: "
        if not r.get("verbs"):
            problems.append(where + "choose at least one verb")
        if r.get("nonResourceURLs"):
            if not cluster_scoped:
                problems.append(where + "non-resource URLs are only allowed in a ClusterRole")
            if r.get("resources") or r.get("apiGroups"):
                problems.append(where + "a rule is either for resources or for URLs, not both")
        else:
            if "apiGroups" not in r:
                problems.append(where + "choose at least one API group")
            if not r.get("resources"):
                problems.append(where + "choose at least one resource")
    return problems


RESOURCE_NAMES_HELP = (
    "<b>Resource names</b> limit the rule to specific objects, matched by their exact <code>metadata.name</code>."
    "<ul>"
    "<li>Empty: the rule applies to every object of the chosen resources.</li>"
    "<li>Exact, case-sensitive names only. No wildcards or patterns.</li>"
    "<li>What a name is depends on the resource: a ConfigMap or Secret name (<code>my-configmap</code>); "
    "a ClusterRole or Role name with <code>bind</code> / <code>escalate</code>; "
    "a user, group or ServiceAccount name with <code>impersonate</code>.</li>"
    "<li>A subresource (<code>pods/log</code>) is limited by the name of its parent object.</li>"
    "<li><code>create</code> and <code>deletecollection</code> cannot be limited by name.</li>"
    "<li><code>list</code> and <code>watch</code> are only limited when the client sends a matching "
    "<code>metadata.name</code> field selector.</li>"
    "<li>Not used by non-resource URL rules.</li>"
    "</ul>")
AGGREGATE_PREFIX = "rbac.authorization.k8s.io/aggregate-to-"
DEFAULTS_LABEL = ("kubernetes.io/bootstrapping", "rbac-defaults")
AUTOUPDATE = "rbac.authorization.kubernetes.io/autoupdate"


def role_warnings(raw):
    """Why changing or deleting this Role/ClusterRole needs care (Kubernetes RBAC docs), as readable sentences.

    - labelled `rbac.authorization.k8s.io/aggregate-to-<role>: "true"`: its rules are merged into that aggregated
      ClusterRole (admin / edit / view are built this way), so every holder of it gets the change;
    - a default role (`system:` prefix or the rbac-defaults label): the API server restores missing default
      permissions at start-up unless the autoupdate annotation is "false"."""
    meta = raw.get("metadata") or {}
    labels, annotations = meta.get("labels") or {}, meta.get("annotations") or {}
    out = []
    feeds = sorted(k[len(AGGREGATE_PREFIX):] for k, v in labels.items()
                   if k.startswith(AGGREGATE_PREFIX) and str(v).lower() == "true")
    if feeds:
        out.append("This role is aggregated into the ClusterRole " + ", ".join(feeds) + ": its rules are merged "
                   "into that role by the control plane, so a change here changes what everyone who holds it is "
                   "allowed to do, in every namespace.")
    if labels.get(DEFAULTS_LABEL[0]) == DEFAULTS_LABEL[1] or str(meta.get("name", "")).startswith("system:"):
        reconcile = ("Automatic reconciliation is switched off for it (autoupdate=false), so a bad edit stays."
                     if str(annotations.get(AUTOUPDATE, "")).lower() == "false" else
                     "At every API server start-up missing default permissions are added back, so removing "
                     "permissions does not last; wrong edits can still break the cluster's own components.")
        out.append("This is a default (system) role. The system: prefix is reserved for Kubernetes. " + reconcile)
    return out


def aggregated_note(raw):
    """Shown when editing a ClusterRole that has an aggregationRule: its `rules` are overwritten by the controller."""
    if not raw.get("aggregationRule"):
        return ""
    return ("This ClusterRole is aggregated: the control plane overwrites its rules with those of the ClusterRoles "
            "its aggregationRule selects. Edit the selectors (clusterRoleSelectors), not the rules.")


def rule_notes(rule):
    """Things that are valid but limited, from the docs: resourceNames cannot restrict create or deletecollection."""
    r = normalize_rule(rule)
    notes = []
    names, verbs = r.get("resourceNames"), set(r.get("verbs", []))
    if names and verbs & {"create", "deletecollection"}:
        notes.append("resource names cannot restrict create or deletecollection requests, so they have no effect "
                     "on those verbs")
    if names and verbs & {"list", "watch"}:
        notes.append("list and watch are only limited by resource names when the client sends a matching "
                     "metadata.name field selector")
    return notes


def confirm_risky(parent, kind, name, warnings, action="Save"):
    """Ask before changing or deleting a role that needs care (default answer No). True when there is nothing to ask."""
    if not warnings:
        return True
    text = f"{kind} {name}\n\n" + "\n\n".join(warnings) + f"\n\n{action} anyway?"
    return QMessageBox.question(parent, f"{action} {kind}?", text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def resource_label(name, group):
    return f"{name}.{group}" if group and name != ALL else name


def rule_summary(rule, width=90):
    r = normalize_rule(rule)
    verbs = ",".join(r.get("verbs", [])) or "(no verbs)"
    if r.get("nonResourceURLs") and not r.get("resources"):
        what = ", ".join(r["nonResourceURLs"])
    else:
        groups = r.get("apiGroups", [])
        single = groups[0] if len(groups) == 1 else ""
        what = ", ".join(resource_label(x, single) for x in r.get("resources", [])) or "(no resources)"
        if len(groups) > 1:
            what += f" [{len(groups)} API groups]"
    text = f"{verbs}  on  {what}"
    return text if len(text) <= width else text[:width - 1] + "…"


def grants_everything(rules):
    return any(ALL in (r.get("verbs") or []) and ALL in (r.get("resources") or []) and ALL in (r.get("apiGroups") or [])
               for r in rules)


# ---- widgets ----------------------------------------------------------------------
class Picker(QWidget):
    """A list of chosen values, filled either by searching (`search` given) or by typing (free text).

    search(query) -> (items, more); describe(item) -> (value, text, extra). `special` entries [(value, text)] are offered
    first (e.g. "*"). The chosen values are `values()`; `picked` carries (value, extra) for what was just added."""

    changed = Signal()
    picked = Signal(object)

    def __init__(self, title, search=None, describe=None, special=(), label=None, placeholder="", help="",
                 direct=False):
        super().__init__()
        self.direct = direct  # search-and-pick only: no list of chosen values; `picked` is all the caller needs
        self.search, self.describe, self.special = search, describe, list(special)
        self.label = label or (lambda v: v)
        self._values = []
        self.title = QLabel(f"<b>{title}</b>")
        # a "?" next to the title; hovering shows the help, clicking shows it at once (tooltips need a hover)
        self.help_button = QToolButton(text="?")
        self.help_button.setAutoRaise(True)
        self.help_button.setCursor(Qt.WhatsThisCursor)
        self.help_button.setToolTip(help)
        self.help_button.setVisible(bool(help))
        self.help_button.clicked.connect(lambda: QToolTip.showText(QCursor.pos(), help, self.help_button))
        title_row = QHBoxLayout()
        title_row.addWidget(self.title)
        title_row.addWidget(self.help_button)
        title_row.addStretch(1)
        self.query = QLineEdit(placeholderText=placeholder or ("Search…" if search else "Type a value and press Enter"),
                               clearButtonEnabled=True)
        self.results = QListWidget()
        self.results.setSelectionMode(QListWidget.ExtendedSelection)
        self.results.setFixedHeight(96)
        self.add_button = QPushButton("Add selected" if search else "Add")
        self.chosen = QListWidget()
        self.chosen.setSelectionMode(QListWidget.ExtendedSelection)
        self.chosen.setFixedHeight(70)
        self.remove_button = QPushButton("Remove")
        row = QHBoxLayout()
        row.addWidget(self.query, 1)
        row.addWidget(self.add_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(title_row)
        lay.addLayout(row)
        if search:
            lay.addWidget(self.results)
        else:
            self.results.hide()
        chosen_row = QHBoxLayout()
        chosen_row.addWidget(self.chosen, 1)
        chosen_row.addWidget(self.remove_button, 0, Qt.AlignTop)
        if direct:
            self.chosen.hide()
            self.remove_button.hide()
        lay.addLayout(chosen_row)
        self._timer = QTimer(self, singleShot=True, interval=SEARCH_DELAY_MS)
        self._timer.timeout.connect(self.refresh)
        self.query.textChanged.connect(lambda _t: self._timer.start() if self.search else None)
        self.query.returnPressed.connect(self._add_from_query if not search else self._add_selected)
        self.add_button.clicked.connect(self._add_from_query if not search else self._add_selected)
        self.results.itemDoubleClicked.connect(lambda _i: self._add_selected())
        self.remove_button.clicked.connect(self._remove_selected)
        self.chosen.itemDoubleClicked.connect(lambda _i: self._remove_selected())

    # chosen values
    def values(self):
        return list(self._values)

    def set_values(self, values, emit=False):
        self._values = list(dict.fromkeys(values))
        self.chosen.clear()
        for v in self._values:
            item = QListWidgetItem(self.label(v))
            item.setData(Qt.UserRole, v)
            self.chosen.addItem(item)
        if emit:
            self.changed.emit()

    def add_values(self, values):
        new = [v for v in values if v not in self._values]
        if new:
            self.set_values(self._values + new, emit=True)
        return new

    def _remove_selected(self):
        drop = {i.data(Qt.UserRole) for i in self.chosen.selectedItems()}
        if drop:
            self.set_values([v for v in self._values if v not in drop], emit=True)

    # search
    def refresh(self):
        if not self.search:
            return
        text = self.query.text().strip().lower()
        self.results.clear()
        for value, label in self.special:
            if not text or text in label.lower():
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, (value, None))
                self.results.addItem(item)
        items, more = self.search(self.query.text())
        for it in items:
            value, label, extra = self.describe(it)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (value, extra))
            self.results.addItem(item)
        if more:
            hint = QListWidgetItem(f"… more than {RESULT_LIMIT} matches — type to narrow the search")
            hint.setFlags(Qt.NoItemFlags)
            self.results.addItem(hint)
        elif self.results.count() == 0:
            hint = QListWidgetItem("No matches (the list may still be loading)")
            hint.setFlags(Qt.NoItemFlags)
            self.results.addItem(hint)

    def _add_selected(self):
        chosen = [i.data(Qt.UserRole) for i in self.results.selectedItems() if i.data(Qt.UserRole)]
        added = self.add_values([v for v, _ in chosen])
        if added:
            self.picked.emit([(v, extra) for v, extra in chosen if v in added])
        if self.direct:
            self.set_values([])  # nothing is kept here: the caller owns the list, so the same value can be picked again

    def _add_from_query(self):
        value = self.query.text().strip()
        if value and self.add_values([value]):
            self.picked.emit([(value, None)])
        self.query.clear()


class RoleEditDialog(QDialog):
    """Edit the rules of a Role/ClusterRole. After Save, `result_body` is the manifest with the new rules; if the user
    chose "Edit as YAML…" instead, `wants_yaml` is True and the dialog was rejected."""

    def __init__(self, kind, raw, loader, parent=None):
        super().__init__(parent)
        self.kind, self.loader, self.index = kind, loader, loader.index
        self.cluster_scoped = kind == "ClusterRole"
        self._raw = raw
        self.result_body, self.wants_yaml = None, False
        self.rules = [normalize_rule(r) for r in (raw.get("rules") or [])]
        self._current = -1
        self._loading = False
        name = (raw.get("metadata") or {}).get("name", "")
        self.setWindowTitle(f"Edit {kind} {name}")
        self.resize(980, 680)

        self.rule_list = QListWidget()
        self.add_rule_button = QPushButton("Add rule")
        self.copy_rule_button = QPushButton("Duplicate")
        self.remove_rule_button = QPushButton("Remove")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(QLabel("<b>Rules</b>"))
        left_lay.addWidget(self.rule_list, 1)
        for row in ((self.add_rule_button, self.copy_rule_button), (self.remove_rule_button,),
                    (self.up_button, self.down_button)):
            line = QHBoxLayout()
            for b in row:
                line.addWidget(b)
            left_lay.addLayout(line)

        self.type_box = QComboBox()
        self.type_box.addItems(["Resources", "Non-resource URLs"])
        self.groups = Picker("API groups", lambda q: self.index.search_groups(q, RESULT_LIMIT),
                             lambda g: (g, g or "(core)", None), special=[(ALL, "* (all API groups)")],
                             label=lambda g: g or "(core)", placeholder="Search API groups…")
        self.resources = Picker("Resources (built-in, custom and subresources)", self._search_resources,
                                lambda r: (r.name, f"{r.name}    [{r.group or 'core'}]    {r.kind}", r.group),
                                special=[(ALL, "* (all resources)")], placeholder="Search by name, kind or group…")
        self.only_groups = QCheckBox("Only resources from the API groups chosen above")
        self.only_groups.setChecked(True)
        self.names = Picker("Resource names (optional: limit to these objects)", help=RESOURCE_NAMES_HELP)
        self.urls = Picker("Non-resource URLs (for example /healthz or /metrics*; only effective in a ClusterRole "
                           "bound with a ClusterRoleBinding)")
        self.verb_boxes = {}
        verbs = QGroupBox("Verbs")
        verb_lay = QVBoxLayout(verbs)
        line = QHBoxLayout()
        for verb in (ALL,) + STANDARD_VERBS + SPECIAL_VERBS:
            box = QCheckBox("* (all)" if verb == ALL else verb)
            box.toggled.connect(lambda on, v=verb: self._verb_toggled(v, on))
            self.verb_boxes[verb] = box
            line.addWidget(box)
            if len(self.verb_boxes) in (5, 10):
                verb_lay.addLayout(line)
                line = QHBoxLayout()
        line.addStretch(1)
        verb_lay.addLayout(line)
        self.extra_verbs = QHBoxLayout()
        verb_lay.addLayout(self.extra_verbs)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.warnings = role_warnings(raw)
        self.banner = QLabel("<b>Take care:</b> " + "<br><br>".join(self.warnings))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(self.warnings))
        form = QWidget()
        form_lay = QVBoxLayout(form)
        form_lay.addWidget(self.type_box)
        form_lay.addWidget(self.groups)
        form_lay.addWidget(self.resources)
        form_lay.addWidget(self.only_groups)
        form_lay.addWidget(self.names)
        form_lay.addWidget(self.urls)
        form_lay.addWidget(verbs)
        form_lay.addWidget(self.hint)
        form_lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(form)
        if not self.cluster_scoped:
            self.type_box.hide()
            self.urls.hide()

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 680])

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.reload_button = QPushButton("Reload API list")
        self.yaml_button = QPushButton("Edit as YAML…")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.reload_button)
        bottom.addWidget(self.yaml_button)
        bottom.addWidget(buttons)
        top = QLabel("Only the rules are edited here; labels, annotations and the rest of the manifest are kept. "
                     "Saving replaces the live object, and the API server refuses rules you could not grant yourself.")
        top.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(top)
        lay.addWidget(self.banner)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)

        self.add_rule_button.clicked.connect(self._add_rule)
        self.copy_rule_button.clicked.connect(self._copy_rule)
        self.remove_rule_button.clicked.connect(self._remove_rule)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.rule_list.currentRowChanged.connect(self._select)
        self.type_box.currentIndexChanged.connect(self._edited)
        self.groups.changed.connect(self._groups_changed)
        self.resources.changed.connect(self._edited)
        self.resources.picked.connect(self._resources_picked)
        self.only_groups.toggled.connect(lambda _on: self.resources.refresh())
        self.names.changed.connect(self._edited)
        self.urls.changed.connect(self._edited)
        self.reload_button.clicked.connect(lambda: self.loader.start(force=True))
        self.yaml_button.clicked.connect(self._as_yaml)
        self._refresh_timer = QTimer(self, singleShot=True, interval=400)
        self._refresh_timer.timeout.connect(self._index_changed)
        loader.progress.connect(self._progress)
        loader.finished.connect(self._index_changed)

        self._fill_list()
        self._update_status()
        self._select(0 if self.rules else -1)
        self.groups.refresh()
        self.resources.refresh()

    # ---- the index ----
    def _search_resources(self, query):
        groups = [g for g in self.groups.values()]
        if not self.only_groups.isChecked() or not groups or ALL in groups:
            groups = ()
        return self.index.search(query, groups, RESULT_LIMIT)

    def _progress(self, *_):
        self._update_status()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _index_changed(self):
        self._update_status()
        self.groups.refresh()
        self.resources.refresh()

    def _update_status(self):
        self.status.setText(self.loader.status_text())
        self.reload_button.setEnabled(self.loader.state != "loading")

    def done(self, code):
        for signal, slot in ((self.loader.progress, self._progress), (self.loader.finished, self._index_changed)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(code)

    # ---- the rule list ----
    def _fill_list(self):
        self.rule_list.blockSignals(True)
        self.rule_list.clear()
        for rule in self.rules:
            self.rule_list.addItem(rule_summary(rule))
        self.rule_list.blockSignals(False)

    def _select(self, row):
        self._current = row if 0 <= row < len(self.rules) else -1
        self.rule_list.blockSignals(True)
        self.rule_list.setCurrentRow(self._current)
        self.rule_list.blockSignals(False)
        self.scroll.setEnabled(self._current >= 0)
        for b in (self.copy_rule_button, self.remove_rule_button, self.up_button, self.down_button):
            b.setEnabled(self._current >= 0)
        self._load_rule()

    def _add_rule(self):
        self._commit()
        self.rules.append({"apiGroups": [""], "verbs": ["get", "list", "watch"]})
        self._fill_list()
        self._select(len(self.rules) - 1)

    def _copy_rule(self):
        if self._current < 0:
            return
        self._commit()
        self.rules.insert(self._current + 1, deepcopy(self.rules[self._current]))
        self._fill_list()
        self._select(self._current + 1)

    def _remove_rule(self):
        if self._current < 0:
            return
        del self.rules[self._current]
        self._fill_list()
        self._select(min(self._current, len(self.rules) - 1))

    def _move(self, step):
        i, j = self._current, self._current + step
        if i < 0 or not 0 <= j < len(self.rules):
            return
        self._commit()
        self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
        self._fill_list()
        self._select(j)

    # ---- the rule form ----
    def _load_rule(self):
        self._loading = True
        try:
            rule = self.rules[self._current] if self._current >= 0 else {}
            self.type_box.setCurrentIndex(1 if self.cluster_scoped and is_url_rule(rule) else 0)
            self.groups.set_values(rule.get("apiGroups", []))
            self.resources.set_values(rule.get("resources", []))
            self.names.set_values(rule.get("resourceNames", []))
            self.urls.set_values(rule.get("nonResourceURLs", []))
            verbs = rule.get("verbs", [])
            while self.extra_verbs.count():
                widget = self.extra_verbs.takeAt(0).widget()
                if widget:
                    self.verb_boxes.pop(widget.text(), None)
                    widget.deleteLater()
            for verb in verbs:
                if verb not in self.verb_boxes:  # a verb this form does not list (kept, not lost)
                    box = QCheckBox(verb)
                    box.toggled.connect(lambda on, v=verb: self._verb_toggled(v, on))
                    self.verb_boxes[verb] = box
                    self.extra_verbs.addWidget(box)
            for verb, box in self.verb_boxes.items():
                box.setChecked(verb in verbs)
            self._show_type()
        finally:
            self._loading = False
        self._hint()
        self.groups.refresh()
        self.resources.refresh()

    def _show_type(self):
        urls = self.cluster_scoped and self.type_box.currentIndex() == 1
        for w in (self.groups, self.resources, self.only_groups, self.names):
            w.setVisible(not urls)
        self.urls.setVisible(bool(urls))

    def _verb_toggled(self, verb, on):
        if self._loading:
            return
        if on:  # "*" excludes the single verbs and the other way round
            self._loading = True
            for v, box in self.verb_boxes.items():
                if (verb == ALL and v != ALL) or (verb != ALL and v == ALL):
                    box.setChecked(False)
            self._loading = False
        self._edited()

    def _groups_changed(self):
        self.resources.refresh()
        self._edited()

    def _resources_picked(self, picks):
        """Choosing a resource from a group that is not in the rule adds that group (the rule needs both)."""
        groups = self.groups.values()
        if ALL in groups:
            return
        missing = [g for _v, g in picks if g is not None and g not in groups]
        if missing:
            self.groups.add_values(missing)

    def _form_rule(self):
        rule = {"verbs": [v for v, box in self.verb_boxes.items() if box.isChecked()]}
        if self.cluster_scoped and self.type_box.currentIndex() == 1:
            rule["nonResourceURLs"] = self.urls.values()
        else:
            rule.update(apiGroups=self.groups.values(), resources=self.resources.values(),
                        resourceNames=self.names.values())
        return normalize_rule(rule)

    def _commit(self):
        if 0 <= self._current < len(self.rules):
            self.rules[self._current] = self._form_rule()

    def _edited(self, *_):
        if self._loading or self._current < 0:
            return
        self._show_type()
        self._commit()
        item = self.rule_list.item(self._current)
        if item:
            item.setText(rule_summary(self.rules[self._current]))
        self._hint()

    def _hint(self):
        if self._current < 0:
            self.hint.setText("")
            return
        problems = validate_rules([self.rules[self._current]], self.cluster_scoped)
        rule = self.rules[self._current]
        text = "; ".join(p.split(": ", 1)[1] for p in problems)
        if not problems and ALL in rule.get("verbs", []) and ALL in rule.get("resources", []):
            text = "This rule grants every verb on every resource of the chosen API groups."
        if not text:
            text = "; ".join(rule_notes(rule)).capitalize()
        self.hint.setText(f"<span style='color:{theme.WARN}'>{text}</span>" if text else "")

    # ---- result ----
    def pending_body(self):
        """The manifest with the rules as they are in the form now (not validated: the YAML editor shows them as they are)."""
        self._commit()
        body = deepcopy(self._raw)
        body["rules"] = [normalize_rule(r) for r in self.rules]
        return body

    def _as_yaml(self):
        leave_for_yaml(self)

    def _save(self):
        self._commit()
        problems = validate_rules(self.rules, self.cluster_scoped)
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        if grants_everything(self.rules) and QMessageBox.question(
                self, "Grant everything?", "A rule gives every verb on every resource in every API group "
                "(cluster-admin). Save anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if not confirm_risky(self, self.kind, self._raw.get("metadata", {}).get("name", ""), self.warnings):
            return
        body = deepcopy(self._raw)
        body["rules"] = [normalize_rule(r) for r in self.rules]
        self.result_body = body
        self.accept()
