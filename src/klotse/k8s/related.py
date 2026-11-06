"""Objects related to one object: its owners (a Pod's ReplicaSet and that one's Deployment), what it owns, and the other
objects it uses or is used by (a Pod's node, service account, ConfigMaps, Secrets and claims; a Service's pods, endpoints and
ingresses; a volume claim's volume and pods; a ConfigMap's or Secret's pods). Pure functions over plain manifest dicts, no Qt,
no cluster access: everything that needs the cluster goes through a `fetch` object with `get(kind, namespace, name)` and
`list(kind, namespace, label_selector=None, field_selector=None)` (the service builds one; tests use fakes).

References are read from manifests only: a Secret's *data* is never fetched, only which pods mention its name."""
from dataclasses import dataclass

CLUSTER_KINDS = ("Node", "PersistentVolume", "StorageClass")  # no namespace
DEPTH_LIMIT = 4  # how far up an owner chain is followed
SHOWN = 50  # entries of one relation kept; the rest is counted ("... and 12 more")
SCAN_LIMIT = 2000  # pods looked at when a ConfigMap, Secret, claim or service account is checked for the pods that use it

KINDS = ("Pod", "ReplicaSet", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Service", "Ingress",
         "PersistentVolumeClaim", "PersistentVolume", "Node", "ConfigMap", "Secret", "ServiceAccount")  # kinds that have a Related view


@dataclass(frozen=True)
class Relation:
    relation: str  # "Owned by", "Owns", "Runs on", "Uses", "Used by", "Selects", "Selected by", "Routes to", "Bound to", "Backed by"
    kind: str
    namespace: str
    name: str
    depth: int = 0  # indentation under the entry above (an owner's owner, a ReplicaSet's pods)
    note: str = ""


def _meta(obj):
    return (obj or {}).get("metadata") or {}


def _spec(obj):
    return (obj or {}).get("spec") or {}


def name_of(obj):
    return _meta(obj).get("name", "")


def namespace_of(obj):
    return _meta(obj).get("namespace", "")


def selector_text(match_labels):
    return ",".join(f"{k}={v}" for k, v in sorted((match_labels or {}).items()))


def selects(selector, labels):
    """True when every pair of a Service-style selector (a plain mapping) is in `labels`; an empty selector selects nothing."""
    return bool(selector) and all((labels or {}).get(k) == v for k, v in selector.items())


def owned_by(obj, uid):
    return bool(uid) and any(o.get("uid") == uid for o in _meta(obj).get("ownerReferences") or [])


def pod_template_spec(obj):
    """The pod spec of a pod or of a workload's template."""
    spec = _spec(obj)
    return spec if "containers" in spec else ((spec.get("template") or {}).get("spec") or {})


# ---- what a pod refers to ----------------------------------------------------------------------------------------
def pod_references(spec):
    """{'ConfigMap': set, 'Secret': set, 'PersistentVolumeClaim': set, 'ServiceAccount': set} named by a pod spec: volumes
    (incl. projected sources), env and envFrom of every container, image pull secrets and the service account."""
    found = {"ConfigMap": set(), "Secret": set(), "PersistentVolumeClaim": set(), "ServiceAccount": set()}
    for volume in spec.get("volumes") or []:
        if volume.get("configMap"):
            found["ConfigMap"].add(volume["configMap"].get("name", ""))
        if volume.get("secret"):
            found["Secret"].add(volume["secret"].get("secretName", ""))
        if volume.get("persistentVolumeClaim"):
            found["PersistentVolumeClaim"].add(volume["persistentVolumeClaim"].get("claimName", ""))
        for source in ((volume.get("projected") or {}).get("sources")) or []:
            if source.get("configMap"):
                found["ConfigMap"].add(source["configMap"].get("name", ""))
            if source.get("secret"):
                found["Secret"].add(source["secret"].get("name", ""))
    for container in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for source in container.get("envFrom") or []:
            if source.get("configMapRef"):
                found["ConfigMap"].add(source["configMapRef"].get("name", ""))
            if source.get("secretRef"):
                found["Secret"].add(source["secretRef"].get("name", ""))
        for var in container.get("env") or []:
            ref = var.get("valueFrom") or {}
            if ref.get("configMapKeyRef"):
                found["ConfigMap"].add(ref["configMapKeyRef"].get("name", ""))
            if ref.get("secretKeyRef"):
                found["Secret"].add(ref["secretKeyRef"].get("name", ""))
    for ref in spec.get("imagePullSecrets") or []:
        found["Secret"].add(ref.get("name", ""))
    found["ServiceAccount"].add(spec.get("serviceAccountName") or spec.get("serviceAccount") or "default")
    return {kind: names - {""} for kind, names in found.items()}


def _cap(relations):
    """At most SHOWN entries of each (relation, kind, depth); a counted note takes the place of the rest."""
    kept, counts, extra = [], {}, {}
    for r in relations:
        key = (r.relation, r.kind, r.depth)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= SHOWN:
            kept.append(r)
        else:
            extra[key] = extra.get(key, 0) + 1
    result, emitted = [], {}
    for r in kept:
        result.append(r)
        key = (r.relation, r.kind, r.depth)
        emitted[key] = emitted.get(key, 0) + 1
        if key in extra and emitted[key] == SHOWN:
            result.append(Relation(r.relation, r.kind, "", f"… and {extra[key]} more", r.depth, "more"))
    return result


# ---- the relations of one object ---------------------------------------------------------------------------------
def owners(raw, fetch, depth=0, namespace=None, limit=DEPTH_LIMIT):
    """The owner chain above `raw`: each owner, then its own owners one level deeper (a Pod's ReplicaSet, then its Deployment)."""
    out = []
    ns = namespace if namespace is not None else namespace_of(raw)
    for ref in _meta(raw).get("ownerReferences") or []:
        kind, name = ref.get("kind", ""), ref.get("name", "")
        out.append(Relation("Owned by", kind, ns, name, depth, "controller" if ref.get("controller") else ""))
        if depth + 1 < limit:
            owner = fetch.get(kind, ns, name)
            if owner:
                out += owners(owner, fetch, depth + 1, ns, limit)
    return out


def _pods_of(raw, fetch, extra_selector=None):
    """The pods a workload owns, directly or through its ReplicaSets/Jobs, found with its own selector to keep the list short."""
    selector = selector_text(((_spec(raw).get("selector") or {}).get("matchLabels")) or extra_selector)
    return fetch.list("Pod", namespace_of(raw), label_selector=selector or None)


def related(kind, raw, fetch):
    """Every Relation for an object of `kind`, in reading order: owners first, then what it owns, uses and is used by."""
    ns, name, uid = namespace_of(raw), name_of(raw), _meta(raw).get("uid")
    out = owners(raw, fetch) if kind != "Node" else []

    def add(relation, k, o, depth=0):
        out.append(Relation(relation, k, "" if k in CLUSTER_KINDS else (namespace_of(o) or ns), name_of(o), depth))

    if kind == "Pod":
        spec = _spec(raw)
        if spec.get("nodeName"):
            out.append(Relation("Runs on", "Node", "", spec["nodeName"]))
        for ref_kind, names in pod_references(spec).items():
            for n in sorted(names):
                out.append(Relation("Uses", ref_kind, ns, n))
        labels = _meta(raw).get("labels") or {}
        for svc in fetch.list("Service", ns):
            if selects(_spec(svc).get("selector"), labels):
                add("Selected by", "Service", svc)
    elif kind == "Deployment":
        sets = [rs for rs in _pods_owner_sets(raw, fetch, "ReplicaSet") if owned_by(rs, uid)]
        pods = _pods_of(raw, fetch)
        for rs in sets:
            add("Owns", "ReplicaSet", rs)
            for pod in pods:
                if owned_by(pod, _meta(rs).get("uid")):
                    add("Owns", "Pod", pod, 1)
    elif kind in ("ReplicaSet", "StatefulSet", "DaemonSet"):
        for pod in _pods_of(raw, fetch):
            if owned_by(pod, uid):
                add("Owns", "Pod", pod)
    elif kind == "Job":
        for pod in _pods_of(raw, fetch, {"job-name": name}):
            if owned_by(pod, uid):
                add("Owns", "Pod", pod)
    elif kind == "CronJob":
        for job in fetch.list("Job", ns):
            if owned_by(job, uid):
                add("Owns", "Job", job)
    elif kind == "Service":
        selector = _spec(raw).get("selector") or {}
        if selector:
            for pod in fetch.list("Pod", ns, label_selector=selector_text(selector)):
                add("Selects", "Pod", pod)
        for s in fetch.list("EndpointSlice", ns, label_selector=f"kubernetes.io/service-name={name}"):
            add("Backed by", "EndpointSlice", s)
        for ing in fetch.list("Ingress", ns):
            if name in ingress_services(ing):
                add("Used by", "Ingress", ing)
    elif kind == "Ingress":
        for svc in sorted(ingress_services(raw)):
            out.append(Relation("Routes to", "Service", ns, svc))
        for tls in _spec(raw).get("tls") or []:
            if tls.get("secretName"):
                out.append(Relation("Uses", "Secret", ns, tls["secretName"]))
    elif kind == "PersistentVolumeClaim":
        if _spec(raw).get("volumeName"):
            out.append(Relation("Bound to", "PersistentVolume", "", _spec(raw)["volumeName"]))
        if _spec(raw).get("storageClassName"):
            out.append(Relation("Uses", "StorageClass", "", _spec(raw)["storageClassName"]))
        out += _used_by_pods("PersistentVolumeClaim", name, ns, fetch)
    elif kind == "PersistentVolume":
        claim = _spec(raw).get("claimRef") or {}
        if claim.get("name"):
            out.append(Relation("Bound to", "PersistentVolumeClaim", claim.get("namespace", ""), claim["name"]))
        if _spec(raw).get("storageClassName"):
            out.append(Relation("Uses", "StorageClass", "", _spec(raw)["storageClassName"]))
    elif kind == "Node":
        for pod in fetch.list("Pod", "", field_selector=f"spec.nodeName={name}"):
            add("Runs", "Pod", pod)
    elif kind in ("ConfigMap", "Secret", "ServiceAccount"):
        out += _used_by_pods(kind, name, ns, fetch)
    return _cap(out)


def _pods_owner_sets(raw, fetch, kind):
    selector = selector_text(((_spec(raw).get("selector") or {}).get("matchLabels")))
    return fetch.list(kind, namespace_of(raw), label_selector=selector or None)


def ingress_services(ingress):
    """The service names an Ingress sends traffic to (rules and default backend)."""
    names = set()
    spec = _spec(ingress)
    default = (spec.get("defaultBackend") or {}).get("service") or {}
    if default.get("name"):
        names.add(default["name"])
    for rule in spec.get("rules") or []:
        for path in ((rule.get("http") or {}).get("paths")) or []:
            backend = (path.get("backend") or {}).get("service") or {}
            if backend.get("name"):
                names.add(backend["name"])
    return names


def _used_by_pods(kind, name, namespace, fetch):
    """The pods of a namespace that mention a ConfigMap, Secret, claim or service account by name (manifests only)."""
    out, scanned = [], 0
    for pod in fetch.list("Pod", namespace):
        scanned += 1
        if scanned > SCAN_LIMIT:
            out.append(Relation("Used by", "Pod", "", f"(only the first {SCAN_LIMIT} pods were checked)", 0, "partial"))
            break
        if name in pod_references(_spec(pod)).get(kind, set()):
            out.append(Relation("Used by", "Pod", namespace, name_of(pod)))
    return out
