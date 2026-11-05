"""Form editors for a Service, an Ingress and a NetworkPolicy, to change an existing object or to create a new one.

All three share `ObjectDialog`: a header, tabs for the object's own fields, a Labels and annotations tab, the name and
namespace of a new object, the same live hint line, "Edit as YAML…" (the form's current values go along as `pending`)
and Save/Create. Only the fields in the form change; everything else in the manifest, and everything inside an entry that
the form does not show, is copied. The data rules are in network_model.py, the tables are `RowsEditor`s."""
from copy import deepcopy
from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout,
                               QWidget)

from . import theme
from .edit_dialog import leave_for_yaml
from .network_model import (AFFINITY, NETWORK_KINDS, PATH_TYPES, POLICY_TYPES, PROTOCOLS, SERVICE_TYPES,  # noqa: F401
                            TRAFFIC_POLICIES, backend_texts, build_backend, build_ingress, build_peer, build_policy,
                            build_service, ingress_rows, ingress_skeleton, peer_row, policy_hints, policy_skeleton,
                            service_hints, service_skeleton, validate_ingress, validate_peer, validate_policy,
                            validate_policy_ports, validate_service)
from .service_account_editor import ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor, validate_pairs
from .workload_model import validate_new
from .workload_widgets import LinesEdit, RowsEditor, TriCombo, page, scrolled

SKELETONS = {"Service": service_skeleton, "Ingress": ingress_skeleton, "NetworkPolicy": policy_skeleton}
INGRESS_ANNOTATIONS = ("nginx.ingress.kubernetes.io/rewrite-target", "nginx.ingress.kubernetes.io/ssl-redirect",
                       "nginx.ingress.kubernetes.io/proxy-body-size", "cert-manager.io/cluster-issuer",
                       "alb.ingress.kubernetes.io/scheme", "alb.ingress.kubernetes.io/target-type")
SERVICE_ANNOTATIONS = ("service.beta.kubernetes.io/aws-load-balancer-type", "service.beta.kubernetes.io/aws-load-balancer-scheme",
                       "cloud.google.com/load-balancer-type", "metallb.universe.tf/address-pool")
NEW_POLICY_NOTE = ("A new policy starts as <b>deny all incoming traffic</b> to every pod in the namespace. Add rules on "
                   "the Ingress rules tab, or narrow the pods it applies to, before you create it.")


def new_manifest(kind, namespace):
    return SKELETONS[kind](namespace)


def muted(text):
    label = QLabel(text)
    label.setWordWrap(True)
    theme.muted(label)
    return label


class ObjectDialog(QDialog):
    """Edit or create a Service, Ingress or NetworkPolicy. After Save, `result_body` is the manifest; "Edit as YAML…" sets
    `wants_yaml` (the form's manifest in `pending`) and rejects the dialog. Subclasses fill the tabs (`build_pages`), read
    them back (`collect`), and name the model functions (`validate`, `hints`, `build`)."""

    annotation_suggestions = ANNOTATION_SUGGESTIONS
    create_note = ""

    def __init__(self, kind, raw, parent=None, create=False, namespaces=(), classes=None, labels=None, services=None):
        super().__init__(parent)
        self.kind, self._raw, self.create, self.classes = kind, raw, create, classes
        self.labels_dir, self.services_dir = labels, services
        self.result_body, self.wants_yaml, self.pending = None, False, None
        meta = raw.get("metadata") or {}
        self.setWindowTitle(f"New {kind}" if create else f"Edit {kind} {meta.get('name', '')}")
        self.resize(900, 720)
        if create:
            intro = f"New {escape(kind)}. Fields not in this form can be added with Edit as YAML… (the changes made here come along)."
        else:
            intro = (f"{escape(kind)} <b>{escape(str(meta.get('name', '')))}</b> in namespace "
                     f"<b>{escape(str(meta.get('namespace', '')))}</b>. Only the fields in this form change; the rest "
                     "of the manifest is kept.")
        self.head = QLabel(intro)
        self.head.setWordWrap(True)
        self.banner = QLabel(self.create_note if create else "")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(theme.warning_style())
        self.banner.setVisible(bool(create and self.create_note))

        self.name_edit = self.namespace_edit = None
        identity = QFormLayout()
        if create:
            self.name_edit = QLineEdit(placeholderText="lowercase letters, digits and -")
            self.namespace_edit = QComboBox()
            self.namespace_edit.setEditable(True)
            self.namespace_edit.addItems(list(namespaces))
            self.namespace_edit.setCurrentText(str(meta.get("namespace", "")))
            identity.addRow("Name", self.name_edit)
            identity.addRow("Namespace", self.namespace_edit)

        self.tabs = QTabWidget()
        self.annotations = KeyValueEditor("Annotations", self.annotation_suggestions, hidden_keys=(LAST_APPLIED,), height=110)
        self.labels = KeyValueEditor("Labels", height=110)
        self.annotations.set_items(dict(meta.get("annotations") or {}))
        self.labels.set_items(dict(meta.get("labels") or {}))
        for title, widget in self.build_pages(raw):
            self.tabs.addTab(widget, title)
        self.tabs.addTab(page(self.labels, self.annotations), "Labels and annotations")
        self._offer_pod_labels()

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
        outer.addLayout(identity)
        outer.addWidget(self.tabs, 1)
        outer.addLayout(bottom)
        self.yaml_button.clicked.connect(self._as_yaml)
        for editor in (self.labels, self.annotations):
            editor.changed.connect(self._hint)
        if self.name_edit is not None:
            self.name_edit.textChanged.connect(self._hint)
            self.namespace_edit.currentTextChanged.connect(self._hint)

    # ---- for the subclasses
    def build_pages(self, raw):  # -> [(tab title, widget)]
        raise NotImplementedError

    def collect(self):  # -> the values the model functions take
        raise NotImplementedError

    def validate(self, values):
        raise NotImplementedError

    def hints(self, values):
        return []

    def build(self, values, new):
        raise NotImplementedError

    def track(self, *signals):
        """Refresh the hint line whenever one of these signals fires."""
        for signal in signals:
            signal.connect(self._hint)

    # ---- state
    def _new(self):
        if not self.create:
            return None
        return {"name": self.name_edit.text(), "namespace": self.namespace_edit.currentText()}

    def values(self):
        return {**self.collect(), "labels": self.labels.items(), "annotations": self.annotations.items(),
                "labels_pairs": self.labels.pairs(), "annotations_pairs": self.annotations.pairs()}

    def problems(self, values=None):
        values = values or self.values()
        return (self.validate(values) + validate_pairs(values["labels_pairs"], "label")
                + validate_pairs(values["annotations_pairs"], "annotation")
                + (validate_new(self.kind, self._new()) if self.create else []))

    def pending_body(self):
        """The manifest for the form as it is now, valid or not (the YAML editor shows it as it is)."""
        try:
            return self.build(self.values(), self._new())
        except (ValueError, KeyError, IndexError, TypeError):
            return None

    def _offer_pod_labels(self):
        """The selector tables offer the labels found on the namespace's pods once they are loaded."""
        editor = getattr(self, "selector", None) or getattr(self, "pod_selector", None)
        if self.labels_dir is None or editor is None:
            return

        def fill():
            if self.labels_dir.state == "done":
                editor.set_choices(self.labels_dir.choices())
        self.labels_dir.finished.connect(fill)
        fill()

    def _hint(self, *_args):
        if not hasattr(self, "hint"):  # a signal fired while the tabs were still being built
            return
        values = self.values()
        problems = self.problems(values)
        if problems:
            self.hint.setText(f"<span style='color:{theme.WARN}'>{escape(problems[0])}</span>"
                              + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""))
            return
        soft = self.hints(values)
        self.hint.setText(escape(soft[0]) if soft else "")

    def _as_yaml(self):
        leave_for_yaml(self)

    def _save(self):
        problems = self.problems()
        if problems:
            QMessageBox.warning(self, "Cannot save", "\n".join(problems))
            return
        self.result_body = self.build(self.values(), self._new())
        self.accept()


# ---- Service ---------------------------------------------------------------------------------------------
class ServiceDialog(ObjectDialog):
    annotation_suggestions = (*SERVICE_ANNOTATIONS, *ANNOTATION_SUGGESTIONS)

    def build_pages(self, raw):
        spec = raw.get("spec") or {}
        self.type_box = QComboBox()
        self.type_box.addItems(SERVICE_TYPES)
        self.type_box.setCurrentText(spec.get("type", "ClusterIP") if spec.get("type", "ClusterIP") in SERVICE_TYPES else "ClusterIP")
        self.type_note = muted("")
        self.external_name = QLineEdit(str(spec.get("externalName", "")), placeholderText="db.example.com")
        self.selector = KeyValueEditor("Selector (pods with these labels receive the traffic)", height=90)
        self.selector.set_items(dict(spec.get("selector") or {}))
        self.ports = RowsEditor(
            "Ports", [("name", "Name", "text", ()), ("port", "Port", "int", ()), ("targetPort", "Target port", "port", ()),
                      ("protocol", "Protocol", "choice", ("", *PROTOCOLS)), ("nodePort", "Node port", "int", ()),
                      ("appProtocol", "App protocol", "text", ())],
            spec.get("ports") or [], new_entry={"port": 80, "targetPort": 80, "protocol": "TCP"}, add_text="Add port", height=130,
            note="Target port: the pod's port, a number or the name the container gave it. Node port: only for NodePort and "
                 "LoadBalancer; leave it empty to have one assigned.")
        main = QFormLayout()
        main.addRow("Type", self.type_box)
        main.addRow("", self.type_note)
        main.addRow("External name", self.external_name)
        self.affinity = QComboBox()
        self.affinity.addItems(AFFINITY)
        self.affinity.setCurrentText(spec.get("sessionAffinity", "None") if spec.get("sessionAffinity") in AFFINITY else "None")
        timeout = ((spec.get("sessionAffinityConfig") or {}).get("clientIP") or {}).get("timeoutSeconds")
        self.affinity_timeout = QLineEdit("" if timeout is None else str(timeout), placeholderText="10800 (the default)")
        self.ext_policy = QComboBox()
        self.ext_policy.addItems(["Not set" if p == "" else p for p in TRAFFIC_POLICIES])
        self.ext_policy.setCurrentIndex(TRAFFIC_POLICIES.index(spec["externalTrafficPolicy"])
                                        if spec.get("externalTrafficPolicy") in TRAFFIC_POLICIES else 0)
        self.ext_policy.setToolTip("Local keeps the client's address and avoids an extra hop, but only nodes that run a "
                                   "pod answer.")
        self.int_policy = QComboBox()
        self.int_policy.addItems(["Not set" if p == "" else p for p in TRAFFIC_POLICIES])
        self.int_policy.setCurrentIndex(TRAFFIC_POLICIES.index(spec["internalTrafficPolicy"])
                                        if spec.get("internalTrafficPolicy") in TRAFFIC_POLICIES else 0)
        self.source_ranges = LinesEdit(spec.get("loadBalancerSourceRanges") or [], "One CIDR per line, e.g. 10.0.0.0/8")
        self.publish = TriCombo(spec.get("publishNotReadyAddresses"))
        self.cluster_ip = QLineEdit(placeholderText="empty = assigned; None = headless")
        advanced = QFormLayout()
        if self.create:
            advanced.addRow("Cluster IP", self.cluster_ip)
        else:
            advanced.addRow("Cluster IP", QLabel(escape(str(spec.get("clusterIP", "")))))
            advanced.addRow("", muted("The cluster IP cannot be changed after the service is created."))
        advanced.addRow("Session affinity", self.affinity)
        advanced.addRow("Affinity timeout (s)", self.affinity_timeout)
        advanced.addRow("External traffic policy", self.ext_policy)
        advanced.addRow("Internal traffic policy", self.int_policy)
        advanced.addRow("Allowed source ranges", self.source_ranges)
        advanced.addRow("Publish not-ready addresses", self.publish)
        self.type_box.currentTextChanged.connect(self._type_changed)
        self.affinity.currentTextChanged.connect(self._type_changed)
        self.track(self.type_box.currentTextChanged, self.external_name.textChanged, self.selector.changed,
                   self.ports.changed, self.affinity_timeout.textChanged, self.cluster_ip.textChanged,
                   self.source_ranges.textChanged, self.affinity.currentTextChanged)
        self._type_changed()
        return [("Service", scrolled(page(main, self.selector, self.ports))), ("Advanced", scrolled(page(advanced)))]

    def _type_changed(self, *_args):
        kind = self.type_box.currentText()
        notes = {"ClusterIP": "Reachable only inside the cluster.",
                 "NodePort": "Also reachable on a port of every node.",
                 "LoadBalancer": "Also gets a load balancer from the cloud provider.",
                 "ExternalName": "A DNS alias to another name: no selector, no ports needed."}
        self.type_note.setText(notes[kind])
        self.external_name.setEnabled(kind == "ExternalName")
        self.selector.setEnabled(kind != "ExternalName")
        self.ext_policy.setEnabled(kind in ("NodePort", "LoadBalancer"))
        self.source_ranges.setEnabled(kind == "LoadBalancer")
        self.affinity_timeout.setEnabled(self.affinity.currentText() == "ClientIP")

    def collect(self):
        return {"type": self.type_box.currentText(), "external_name": self.external_name.text(),
                "selector": self.selector.items(), "selector_pairs": self.selector.pairs(), "ports": self.ports.entries(),
                "cluster_ip": self.cluster_ip.text() if self.create else str((self._raw.get("spec") or {}).get("clusterIP", "")),
                "affinity": self.affinity.currentText(), "affinity_timeout": self.affinity_timeout.text(),
                "ext_policy": TRAFFIC_POLICIES[self.ext_policy.currentIndex()],
                "int_policy": TRAFFIC_POLICIES[self.int_policy.currentIndex()], "source_ranges": self.source_ranges.lines(),
                "publish_not_ready": self.publish.value()}

    def validate(self, values):
        return validate_service(values, self.create)

    def hints(self, values):
        return service_hints(values)

    def build(self, values, new):
        return build_service(self._raw, values, new)


# ---- Ingress ----------------------------------------------------------------------------------------------
def _rule_load(entry):
    name, port = backend_texts((entry.get("path") or {}).get("backend"))
    path = entry.get("path") or {}
    return {"host": entry.get("host", ""), "path": path.get("path", ""), "pathType": path.get("pathType", ""),
            "service": name, "port": port}


def _rule_dump(original, texts):
    path = deepcopy((original or {}).get("path") or {})
    for key in ("path", "pathType"):
        if texts[key].strip():
            path[key] = texts[key].strip()
        else:
            path.pop(key, None)
    path["backend"] = build_backend(path.get("backend"), texts["service"], texts["port"])
    return {"host": texts["host"].strip(), "path": path}


def _tls_load(entry):
    return {"hosts": ", ".join(entry.get("hosts") or []), "secretName": entry.get("secretName", "")}


def _tls_dump(original, texts):
    entry = deepcopy(original or {})
    hosts = [h.strip() for h in texts["hosts"].split(",") if h.strip()]
    if hosts:
        entry["hosts"] = hosts
    else:
        entry.pop("hosts", None)
    if texts["secretName"].strip():
        entry["secretName"] = texts["secretName"].strip()
    else:
        entry.pop("secretName", None)
    return entry


class IngressDialog(ObjectDialog):
    annotation_suggestions = INGRESS_ANNOTATIONS

    def build_pages(self, raw):
        spec = raw.get("spec") or {}
        self.ingress_class = QComboBox()
        self.ingress_class.setEditable(True)
        self.ingress_class.lineEdit().setPlaceholderText("e.g. nginx (empty = the cluster default)")
        self.ingress_class.setCurrentText(str(spec.get("ingressClassName", "")))
        self.class_note = muted("")
        self._fill_classes()
        if self.classes is not None:
            self.classes.finished.connect(self._fill_classes)
        self.rules = RowsEditor(
            "Rules", [("host", "Host", "text", ()), ("path", "Path", "text", ()), ("pathType", "Path type", "choice", PATH_TYPES),
                      ("service", "Service", "suggest", ()), ("port", "Port", "port", ())],
            ingress_rows(spec), new_entry={"host": "", "path": {"path": "/", "pathType": "Prefix",
                                                                  "backend": {"service": {"name": "", "port": {"number": 80}}}}},
            add_text="Add rule", load=_rule_load, dump=_rule_dump, height=170,
            note="Host: empty matches every host. Port: a number or the service's port name. Prefix matches the path and "
                 "everything below it; Exact only that path.")
        name, port = backend_texts(spec.get("defaultBackend"))
        self.default_service = QComboBox()
        self.default_service.setEditable(True)
        self.default_service.lineEdit().setPlaceholderText("service name (empty = none)")
        self.default_service.setCurrentText(name)
        self._fill_services()
        if self.services_dir is not None:
            self.services_dir.finished.connect(self._fill_services)
        self.default_port = QLineEdit(port, placeholderText="80 or a port name")
        self.tls = RowsEditor("TLS", [("hosts", "Hosts (comma separated)", "text", ()), ("secretName", "Secret", "text", ())],
                              spec.get("tls") or [], new_entry={}, add_text="Add TLS entry", load=_tls_load, dump=_tls_dump,
                              height=110, note="The secret holds the certificate (type kubernetes.io/tls) and must be in the "
                                               "ingress's namespace.")
        top = QFormLayout()
        top.addRow("Ingress class", self.ingress_class)
        top.addRow("", self.class_note)
        top.addRow("Default backend service", self.default_service)
        top.addRow("Default backend port", self.default_port)
        self.track(self.ingress_class.editTextChanged, self.rules.changed, self.default_service.editTextChanged,
                   self.default_port.textChanged, self.tls.changed)
        return [("Rules", scrolled(page(top, self.rules))), ("TLS", page(self.tls))]

    def _fill_services(self):
        """The namespace's services in the rule table's Service column and the default backend box (typing stays possible)."""
        if self.services_dir is None or self.services_dir.state != "done":
            return
        names = sorted(self.services_dir.entries)
        self.rules.set_choices("service", names)
        current = self.default_service.currentText()
        self.default_service.blockSignals(True)
        self.default_service.clear()
        self.default_service.addItems(["", *names])
        self.default_service.setCurrentText(current)
        self.default_service.blockSignals(False)

    def _fill_classes(self):
        """List the cluster's IngressClasses in the box (the typed or current name stays; the first entry is empty = the
        cluster's default class). The default class is named in the note under the box."""
        if self.classes is None:
            self.class_note.setVisible(False)
            return
        current = self.ingress_class.currentText()
        self.ingress_class.blockSignals(True)
        self.ingress_class.clear()
        self.ingress_class.addItems(["", *self.classes.names()])
        self.ingress_class.setCurrentText(current)
        self.ingress_class.blockSignals(False)
        default = self.classes.default()
        self.class_note.setText(self.classes.status_text() + (f". Default class: {default}." if default else "."))

    def collect(self):
        return {"class": self.ingress_class.currentText(), "rows": self.rules.entries(),
                "default_backend": {"service": self.default_service.currentText(), "port": self.default_port.text()},
                "tls": self.tls.entries()}

    def validate(self, values):
        return validate_ingress(values)

    def build(self, values, new):
        return build_ingress(self._raw, values, new)


# ---- NetworkPolicy -------------------------------------------------------------------------------------------
class PolicyRules(QWidget):
    """The rules of one direction: the list of rules on the left, the chosen rule's peers and ports on the right."""

    changed = Signal()

    def __init__(self, direction, rules):
        super().__init__()
        self.direction = direction  # "Ingress" (peers are 'from') or "Egress" ('to')
        self.field = "from" if direction == "Ingress" else "to"
        self.items = []  # dicts: original rule, peers editor, ports editor, page
        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.stack = QStackedWidget()
        self.add_button = QPushButton("Add rule")
        self.remove_button = QPushButton("Remove rule")
        self.empty = muted(f"No {direction.lower()} rules yet. Add one to allow traffic.")
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Rules</b>"))
        left.addWidget(self.list, 1)
        left.addWidget(self.add_button)
        left.addWidget(self.remove_button)
        right = QVBoxLayout()
        right.addWidget(self.empty)
        right.addWidget(self.stack, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(left)
        lay.addLayout(right, 1)
        for rule in rules:
            self._add(rule)
        self.add_button.clicked.connect(lambda: self._add({self.field: [{"podSelector": {}}]}, select=True))
        self.remove_button.clicked.connect(self._remove)
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
        if self.items:
            self.list.setCurrentRow(0)
        self._refresh()

    def _add(self, rule, select=False):
        who = "Allowed sources" if self.direction == "Ingress" else "Allowed destinations"
        peers = RowsEditor(who, [("namespace", "Namespace selector", "text", ()), ("pod", "Pod selector", "text", ()),
                                 ("cidr", "IP range", "text", ()), ("except", "Except ranges", "text", ())],
                           rule.get(self.field) or [], new_entry={"podSelector": {}}, add_text="Add peer",
                           load=peer_row, dump=build_peer, height=120,
                           note="Selectors: key=value,key2=value2 or all. A line with both selectors means pods in those "
                                "namespaces. An IP range goes on a line of its own. No lines at all: anywhere.")
        ports = RowsEditor("Ports", [("protocol", "Protocol", "choice", ("", *PROTOCOLS)), ("port", "Port", "port", ()),
                                     ("endPort", "End port", "int", ())],
                           rule.get("ports") or [], new_entry={"protocol": "TCP", "port": 80}, add_text="Add port", height=100,
                           note="No ports at all: every port. End port makes a range (the port is its start).")
        widget = page(peers, ports)
        self.items.append({"rule": deepcopy(rule), "peers": peers, "ports": ports})
        self.stack.addWidget(widget)
        self.list.addItem("")
        for editor in (peers, ports):
            editor.changed.connect(self._changed)
        if select:
            self.list.setCurrentRow(len(self.items) - 1)
        self._changed()

    def _remove(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            self.items.pop(row)
            self.list.takeItem(row)
            widget = self.stack.widget(row)
            self.stack.removeWidget(widget)
            widget.deleteLater()
            self._changed()

    def _changed(self, *_args):
        self._refresh()
        self.changed.emit()

    def _refresh(self):
        for i, item in enumerate(self.items):
            self.list.item(i).setText(f"Rule {i + 1}: {item['peers'].count() or 'anywhere'}"
                                      f"{' peer(s)' if item['peers'].count() else ''}, "
                                      f"{item['ports'].count() or 'any'}{' port(s)' if item['ports'].count() else ' port'}")
        self.empty.setVisible(not self.items)
        self.remove_button.setEnabled(bool(self.items))

    def rules(self):
        out = []
        for item in self.items:
            rule = deepcopy(item["rule"])
            for key, editor in ((self.field, item["peers"]), ("ports", item["ports"])):
                entries = editor.entries()
                if entries:
                    rule[key] = entries
                else:
                    rule.pop(key, None)
            out.append(rule)
        return out

    def problems(self):
        out = []
        for i, item in enumerate(self.items, 1):
            for n, row in enumerate(item["peers"].texts(), 1):
                out += validate_peer(f"{self.direction} rule {i}, peer {n}", row)
            out += validate_policy_ports(f"{self.direction} rule {i}", item["ports"].entries())
        return out


class PolicyDialog(ObjectDialog):
    create_note = NEW_POLICY_NOTE

    def build_pages(self, raw):
        spec = raw.get("spec") or {}
        selector = spec.get("podSelector") or {}
        self.pod_selector = KeyValueEditor("Pods this policy applies to (labels)", height=100)
        self.pod_selector.set_items(dict(selector.get("matchLabels") or {}))
        has_egress = bool(spec.get("egress"))
        types = spec.get("policyTypes") or ["Ingress", *(["Egress"] if has_egress else [])]
        self.type_boxes = {t: QCheckBox(f"Control {t.lower()} traffic") for t in POLICY_TYPES}
        for t, box in self.type_boxes.items():
            box.setChecked(t in types)
            box.toggled.connect(self._types_changed)
        self.ingress = PolicyRules("Ingress", spec.get("ingress") or [])
        self.egress = PolicyRules("Egress", spec.get("egress") or [])
        notes = muted("No labels: the policy applies to every pod in the namespace. A selected direction with no rules blocks "
                      "all traffic in that direction; a rule without peers or ports allows everything. Policies add up: "
                      "traffic is allowed if any policy allows it. They only work if the cluster's network plugin enforces them.")
        self.track(self.pod_selector.changed, self.ingress.changed, self.egress.changed,
                   *[box.toggled for box in self.type_boxes.values()])
        self._types_changed()
        return [("Pods and directions", page(self.pod_selector, *self.type_boxes.values(), notes)),
                ("Ingress rules", self.ingress), ("Egress rules", self.egress)]

    def _types_changed(self, *_args):
        self.ingress.setEnabled(self.type_boxes["Ingress"].isChecked())
        self.egress.setEnabled(self.type_boxes["Egress"].isChecked())

    def collect(self):
        return {"pod_selector": self.pod_selector.items(), "pod_selector_pairs": self.pod_selector.pairs(),
                "types": {t: box.isChecked() for t, box in self.type_boxes.items()},
                "ingress": self.ingress.rules(), "egress": self.egress.rules(),
                "rule_problems": [*(self.ingress.problems() if self.type_boxes["Ingress"].isChecked() else []),
                                  *(self.egress.problems() if self.type_boxes["Egress"].isChecked() else [])]}

    def validate(self, values):
        return validate_policy(values)

    def hints(self, values):
        return policy_hints(values)

    def build(self, values, new):
        return build_policy(self._raw, values, new)


DIALOGS = {"Service": ServiceDialog, "Ingress": IngressDialog, "NetworkPolicy": PolicyDialog}


def NetworkEditDialog(kind, raw, parent=None, create=False, namespaces=(), classes=None, labels=None, services=None):  # noqa: N802 - used like a class
    """The editor for a Service, Ingress or NetworkPolicy."""
    return DIALOGS[kind](kind, raw, parent, create=create, namespaces=namespaces, classes=classes, labels=labels, services=services)
