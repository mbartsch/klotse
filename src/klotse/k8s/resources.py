"""Registry of the simple list views (Phase 2/4). Each spec says which API to call and how to turn an
object into a table row, so the service and UI need no per-kind code."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .util import format_age, to_dict

MIN_TS = datetime.min.replace(tzinfo=timezone.utc)


class Row:
    """Generic table row: fixed name/namespace/raw plus arbitrary column values as attributes."""

    def __init__(self, name, namespace, raw, **values):
        self.name, self.namespace, self._raw = name, namespace, raw
        self.__dict__.update(values)

    @property
    def raw(self):
        """The manifest as a dict. May be given as a callable, which is run on first access and cached."""
        if callable(self._raw):
            self._raw = self._raw()
        return self._raw

    @raw.setter
    def raw(self, value):
        self._raw = value

    def __repr__(self):
        return f"Row({self.namespace}/{self.name})"


def _base(o, raw=None, **vals):
    m = o.metadata
    return Row(m.name, m.namespace or "", raw if raw is not None else to_dict(o),
               age=format_age(m.creation_timestamp), **vals)


def _pairs(d):
    return ",".join(f"{k}={v}" for k, v in (d or {}).items())


def map_daemonset(d):
    s = d.status
    return _base(d, desired=s.desired_number_scheduled or 0, current=s.current_number_scheduled or 0,
                 ready=s.number_ready or 0, up_to_date=s.updated_number_scheduled or 0,
                 available=s.number_available or 0, node_selector=_pairs(d.spec.template.spec.node_selector))


def map_job(j):
    st = j.status
    total = j.spec.completions
    flags = {c.type for c in st.conditions or [] if c.status == "True"}
    status = "Failed" if "Failed" in flags else "Complete" if "Complete" in flags else "Running" if st.active else "Pending"
    duration = format_age(st.start_time, st.completion_time) if st.start_time and st.completion_time else (
        format_age(st.start_time) if st.start_time else "")
    return _base(j, completions=f"{st.succeeded or 0}/{total if total is not None else '-'}", status=status, duration=duration)


def map_cronjob(c):
    st = c.status
    return _base(c, schedule=c.spec.schedule, suspend=str(bool(c.spec.suspend)),
                 active=len(st.active or []) if st else 0,
                 last_schedule=format_age(st.last_schedule_time) if st and st.last_schedule_time else "never")


def map_configmap(c):
    return _base(c, keys=len(c.data or {}) + len(c.binary_data or {}))


def map_secret(s):
    raw = to_dict(s)
    for field in ("data", "stringData"):  # details stay masked until explicitly decoded
        if raw.get(field):
            raw[field] = {k: "<hidden>" for k in raw[field]}
    ann = (raw.get("metadata") or {}).get("annotations") or {}
    if "kubectl.kubernetes.io/last-applied-configuration" in ann:
        ann["kubectl.kubernetes.io/last-applied-configuration"] = "<hidden>"
    row = _base(s, raw=raw, type=s.type or "", keys=len(s.data or {}))
    # Keep encoded values separate from the masked manifest and table columns.
    row.secret_data = dict(s.data or {})
    return row


def _lb_addresses(lb):
    return [i.ip or i.hostname for i in (lb.ingress or [])] if lb else []


def map_service(s):
    sp = s.spec
    external = _lb_addresses(s.status.load_balancer if s.status else None) + list(sp.external_ips or [])
    return _base(s, type=sp.type or "", cluster_ip=sp.cluster_ip or "", external_ip=",".join(external),
                 ports=",".join(f"{p.port}/{p.protocol}" for p in sp.ports or []), selector=_pairs(sp.selector))


def map_ingress(i):
    sp = i.spec
    hosts = ",".join(r.host for r in sp.rules or [] if r.host) or "*"
    return _base(i, ingress_class=sp.ingress_class_name or "", hosts=hosts,
                 address=",".join(_lb_addresses(i.status.load_balancer if i.status else None)),
                 ports="80, 443" if sp.tls else "80")


def _selector(sel):
    """Render a label selector; an empty selector means every pod in the namespace."""
    if sel is None:
        return "<all pods>"
    parts = [f"{k}={v}" for k, v in (sel.match_labels or {}).items()]
    parts += [f"{e.key} {e.operator} [{','.join(e.values or [])}]" for e in sel.match_expressions or []]
    return ",".join(parts) or "<all pods>"


def map_networkpolicy(n):
    sp = n.spec
    # an empty ingress/egress list with the type listed means "deny all" in that direction, so show the counts as-is
    return _base(n, pod_selector=_selector(sp.pod_selector), policy_types=",".join(sp.policy_types or []),
                 ingress=len(sp.ingress or []), egress=len(sp.egress or []))


def map_pvc(p):
    return _base(p, status=p.status.phase or "", volume=p.spec.volume_name or "",
                 capacity=(p.status.capacity or {}).get("storage", ""), access_modes=",".join(p.spec.access_modes or []),
                 storage_class=p.spec.storage_class_name or "")


def map_pv(p):
    ref = p.spec.claim_ref
    return _base(p, capacity=(p.spec.capacity or {}).get("storage", ""), access_modes=",".join(p.spec.access_modes or []),
                 reclaim_policy=p.spec.persistent_volume_reclaim_policy or "", status=p.status.phase or "",
                 claim=f"{ref.namespace}/{ref.name}" if ref else "", storage_class=p.spec.storage_class_name or "")


def map_storageclass(s):
    ann = s.metadata.annotations or {}
    default = ann.get("storageclass.kubernetes.io/is-default-class") == "true"
    return _base(s, provisioner=s.provisioner, reclaim_policy=s.reclaim_policy or "",
                 binding_mode=s.volume_binding_mode or "", expansion=str(bool(s.allow_volume_expansion)),
                 default="yes" if default else "")


def rules_text(rules):
    """One searchable line for RBAC rules: `resources: verbs` per rule, `/path: verbs` for non-resource URLs."""
    parts = []
    for r in rules or []:
        verbs = ",".join(r.verbs or [])
        what = []
        for group in r.api_groups or []:
            for res in r.resources or []:
                what.append(f"{group}/{res}" if group else res)
        # the client renamed this attribute (non_resource_ur_ls before v30-ish, non_resource_urls now); accept both
        urls = getattr(r, "non_resource_urls", None) or getattr(r, "non_resource_ur_ls", None)
        what += list(urls or [])
        if r.resource_names:
            what.append("names=" + ",".join(r.resource_names))
        parts.append(f"{','.join(what) or '-'}: {verbs}")
    return "; ".join(parts)


def subjects_text(subjects):
    out = []
    for s in subjects or []:
        ns = f"{s.namespace}/" if getattr(s, "namespace", None) else ""
        out.append(f"{s.kind}:{ns}{s.name}")
    return ", ".join(out)


def map_role(r):
    return _base(r, rbac_kind="Role", rule_count=len(r.rules or []), rules=rules_text(r.rules))


def map_clusterrole(r):
    aggregated = "yes" if r.aggregation_rule else ""
    return _base(r, rbac_kind="ClusterRole", rule_count=len(r.rules or []), aggregated=aggregated,
                 rules=rules_text(r.rules))


def map_binding(b):
    ref = b.role_ref
    kind = "RoleBinding" if b.metadata.namespace else "ClusterRoleBinding"
    return _base(b, rbac_kind=kind, role=f"{ref.kind}/{ref.name}", subjects=subjects_text(b.subjects), subject_count=len(b.subjects or []))


def map_serviceaccount(s):
    return _base(s, rbac_kind="ServiceAccount", secrets=len(s.secrets or []), automount="" if s.automount_service_account_token is None
                 else str(bool(s.automount_service_account_token)))


def crd_version(crd):
    """The version to browse: the storage version when served, else the first served one."""
    versions = [v for v in crd.spec.versions or [] if v.served]
    return next((v.name for v in versions if v.storage), versions[0].name if versions else "")


def map_crd(c):
    sp = c.spec
    return _base(c, group=sp.group, kind=sp.names.kind, scope=sp.scope, plural=sp.names.plural, version=crd_version(c),
                 versions=",".join(v.name for v in sp.versions or [] if v.served))


def map_custom_object(item, namespaced=True, group=None, kind=None):
    """Row for one instance of a custom resource (a plain dict from the CustomObjects API). For a well-known kind (see
    CUSTOM_COLUMNS) the row also carries that kind's column values; a mapper that fails leaves them out."""
    meta = item.get("metadata") or {}
    extra = {}
    found = CUSTOM_COLUMNS.get((group, kind))
    if found:
        try:
            extra = found[1](item)
        except Exception:  # an odd object must never break the list; its cells stay empty
            extra = {}
    return Row(meta.get("name", ""), meta.get("namespace") or "", item,
               age=format_age(parse_timestamp(meta.get("creationTimestamp"))), **extra)


# ---- instances of well-known custom resources get their own columns --------------------------------------------------
# Only fields that are the same in the v1beta1 and v1 APIs of Karpenter are used, and each one is read defensively: a field
# the installed version does not have just leaves its cell empty. Not checked against a running Karpenter.
def _get(item, *path):
    for key in path:
        item = item.get(key) if isinstance(item, dict) else None
    return item


def _condition(item, *types):
    """'✔ Ready' / '✖ Not ready' from the first of `types` the object reports; '' when it reports none (symbol and words)."""
    found = {c.get("type"): c.get("status") for c in _get(item, "status", "conditions") or [] if isinstance(c, dict)}
    for kind in types:
        if kind in found:
            return f"✔ {kind}" if found[kind] == "True" else f"✖ Not {kind.lower()}"
    return ""


def _text(value):
    return "" if value is None else str(value)


def _nodepool(item):
    status = _get(item, "status") or {}
    resources = status.get("resources") if isinstance(status.get("resources"), dict) else {}
    nodes = status.get("nodes", resources.get("nodes"))
    return dict(nodeclass=_text(_get(item, "spec", "template", "spec", "nodeClassRef", "name")), nodes=_text(nodes),
                cpu=_text(resources.get("cpu")), cpu_limit=_text(_get(item, "spec", "limits", "cpu")),
                weight=_text(_get(item, "spec", "weight")), consolidation=_text(_get(item, "spec", "disruption", "consolidationPolicy")),
                ready=_condition(item, "Ready"))


def _nodeclaim(item):
    labels = _get(item, "metadata", "labels") or {}
    return dict(node=_text(_get(item, "status", "nodeName")), instance_type=_text(labels.get("node.kubernetes.io/instance-type")),
                capacity=_text(labels.get("karpenter.sh/capacity-type")), zone=_text(labels.get("topology.kubernetes.io/zone")),
                nodepool=_text(labels.get("karpenter.sh/nodepool")), ready=_condition(item, "Ready", "Initialized"))


# (API group, kind) -> (columns as (heading, attribute), function giving the attribute values of one object)
CUSTOM_COLUMNS = {
    ("karpenter.sh", "NodePool"): (
        [("Name", "name"), ("NodeClass", "nodeclass"), ("Nodes", "nodes"), ("CPU", "cpu"), ("CPU limit", "cpu_limit"),
         ("Weight", "weight"), ("Consolidation", "consolidation"), ("Ready", "ready"), ("Age", "age")], _nodepool),
    ("karpenter.sh", "NodeClaim"): (
        [("Name", "name"), ("Node", "node"), ("Type", "instance_type"), ("Capacity", "capacity"), ("Zone", "zone"),
         ("NodePool", "nodepool"), ("Ready", "ready"), ("Age", "age")], _nodeclaim),
}


def custom_columns(group, kind):
    """The columns of a well-known custom resource, or None for the plain Name/Namespace/Age ones."""
    found = CUSTOM_COLUMNS.get((group, kind))
    return list(found[0]) if found else None


def parse_timestamp(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def map_event(e, lazy=False):
    """lazy=True defers serializing the manifest until the row's raw is first read (used for long drill-down lists)."""
    io = e.involved_object
    ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
    raw = (lambda: to_dict(e)) if lazy else to_dict(e)
    return Row(e.metadata.name, e.metadata.namespace or "", raw, type=e.type or "", reason=e.reason or "",
               object=f"{io.kind}/{io.name}" if io else "", message=e.message or "", count=e.count or 1,
               last_seen=format_age(ts), ts=ts or MIN_TS)


@dataclass(frozen=True)
class Spec:
    key: str  # navigator key
    title: str
    kind: str
    snake: str  # api method infix, e.g. "daemon_set" -> list_namespaced_daemon_set
    api: str  # core | apps | batch | net | storage
    namespaced: bool
    columns: tuple
    mapper: Callable
    editable: bool = True
    deletable: bool = False  # offers Delete… (the RBAC kinds); delete goes through KubeService.delete_resource


NS = ("Namespace", "namespace")
AGE = ("Age", "age")
SPECS = [
    Spec("daemonsets", "DaemonSets", "DaemonSet", "daemon_set", "apps", True,
         (("Name", "name"), NS, ("Desired", "desired"), ("Current", "current"), ("Ready", "ready"),
          ("Up-to-date", "up_to_date"), ("Available", "available"), ("Node Selector", "node_selector"), AGE), map_daemonset),
    Spec("jobs", "Jobs", "Job", "job", "batch", True,
         (("Name", "name"), NS, ("Completions", "completions"), ("Status", "status"), ("Duration", "duration"), AGE), map_job),
    Spec("cronjobs", "CronJobs", "CronJob", "cron_job", "batch", True,
         (("Name", "name"), NS, ("Schedule", "schedule"), ("Suspend", "suspend"), ("Active", "active"),
          ("Last Schedule", "last_schedule"), AGE), map_cronjob),
    Spec("configmaps", "ConfigMaps", "ConfigMap", "config_map", "core", True,
         (("Name", "name"), NS, ("Keys", "keys"), AGE), map_configmap),
    Spec("secrets", "Secrets", "Secret", "secret", "core", True,
         (("Name", "name"), NS, ("Type", "type"), ("Keys", "keys"), AGE), map_secret),
    Spec("services", "Services", "Service", "service", "core", True,
         (("Name", "name"), NS, ("Type", "type"), ("Cluster IP", "cluster_ip"), ("External IP", "external_ip"),
          ("Ports", "ports"), ("Selector", "selector"), AGE), map_service),
    Spec("ingresses", "Ingresses", "Ingress", "ingress", "net", True,
         (("Name", "name"), NS, ("Class", "ingress_class"), ("Hosts", "hosts"), ("Address", "address"),
          ("Ports", "ports"), AGE), map_ingress),
    Spec("networkpolicies", "Network Policies", "NetworkPolicy", "network_policy", "net", True,
         (("Name", "name"), NS, ("Pod Selector", "pod_selector"), ("Policy Types", "policy_types"),
          ("Ingress rules", "ingress"), ("Egress rules", "egress"), AGE), map_networkpolicy),
    Spec("persistentvolumeclaims", "Persistent Volume Claims", "PersistentVolumeClaim", "persistent_volume_claim", "core", True,
         (("Name", "name"), NS, ("Status", "status"), ("Volume", "volume"), ("Capacity", "capacity"),
          ("Access Modes", "access_modes"), ("Storage Class", "storage_class"), AGE), map_pvc),
    Spec("persistentvolumes", "Persistent Volumes", "PersistentVolume", "persistent_volume", "core", False,
         (("Name", "name"), ("Capacity", "capacity"), ("Access Modes", "access_modes"), ("Reclaim Policy", "reclaim_policy"),
          ("Status", "status"), ("Claim", "claim"), ("Storage Class", "storage_class"), AGE), map_pv),
    Spec("storageclasses", "Storage Classes", "StorageClass", "storage_class", "storage", False,
         (("Name", "name"), ("Provisioner", "provisioner"), ("Reclaim Policy", "reclaim_policy"),
          ("Binding Mode", "binding_mode"), ("Expansion", "expansion"), ("Default", "default"), AGE), map_storageclass),
    Spec("crds", "Custom Resource Definitions", "CustomResourceDefinition", "custom_resource_definition", "ext", False,
         (("Name", "name"), ("Group", "group"), ("Kind", "kind"), ("Scope", "scope"), ("Versions", "versions"), AGE), map_crd),
    Spec("events", "Events", "Event", "event", "core", True,
         (("Type", "type"), ("Reason", "reason"), ("Object", "object"), ("Message", "message"), NS,
          ("Count", "count"), ("Last Seen", "last_seen")), map_event, editable=False),
    # Access control (RBAC). The rules / subjects columns are plain text so the table filter finds e.g. "secrets" or a user.
    Spec("serviceaccounts", "Service Accounts", "ServiceAccount", "service_account", "core", True,
         (("Name", "name"), NS, ("Secrets", "secrets"), ("Automount token", "automount"), AGE),
         map_serviceaccount, deletable=True),
    Spec("roles", "Roles", "Role", "role", "rbac", True,
         (("Name", "name"), NS, ("Rules", "rule_count"), ("Permissions", "rules"), AGE), map_role, deletable=True),
    Spec("rolebindings", "Role Bindings", "RoleBinding", "role_binding", "rbac", True,
         (("Name", "name"), NS, ("Role", "role"), ("Subjects", "subjects"), AGE), map_binding, deletable=True),
    Spec("clusterroles", "Cluster Roles", "ClusterRole", "cluster_role", "rbac", False,
         (("Name", "name"), ("Rules", "rule_count"), ("Aggregated", "aggregated"), ("Permissions", "rules"), AGE),
         map_clusterrole, deletable=True),
    Spec("clusterrolebindings", "Cluster Role Bindings", "ClusterRoleBinding", "cluster_role_binding", "rbac", False,
         (("Name", "name"), ("Role", "role"), ("Subjects", "subjects"), AGE), map_binding, deletable=True),
]
SPEC_BY_KEY = {s.key: s for s in SPECS}
SPEC_BY_KIND = {s.kind: s for s in SPECS}
DELETABLE_KINDS = frozenset(s.kind for s in SPECS if s.deletable)
