"""Objects that nothing seems to use: ConfigMaps, Secrets and PersistentVolumeClaims that no pod, workload template, Ingress or
Service Account refers to (pure, over manifests; no network, no Qt).

"Unused" here means *no object Klotse reads names it*. It is a lead for tidying up, not a verdict: controllers and operators that
find objects by label or by convention (cert-manager, Prometheus, a CRD with a secretRef, Gateway API certificateRefs, scripts) are
invisible to it, and so is anything created for a later deployment. Skipped on purpose because the cluster itself uses them:
`kube-root-ca.crt`, service account token Secrets, Helm release storage Secrets, and the claims a StatefulSet keeps for its
replicas. Secrets arrive as metadata only (the server never sends their data), so a Secret is judged by its name and labels."""
import re

from .related import pod_references

ROOT_CA = ("kube-root-ca.crt", "openshift-service-ca.crt")
KINDS = ("ConfigMap", "Secret", "PersistentVolumeClaim")
FINISHED = ("Succeeded", "Failed")


def _meta(obj):
    return (obj or {}).get("metadata") or {}


def _namespace(obj):
    return _meta(obj).get("namespace", "")


def pod_specs(data):
    """(namespace, pod spec) of every pod and of every workload's pod template: a Deployment scaled to zero still uses its ConfigMap."""
    for pod in data.get("pods") or []:
        yield _namespace(pod), pod.get("spec") or {}
    for key in ("deployments", "statefulsets", "daemonsets", "replicasets", "jobs"):
        for obj in data.get(key) or []:
            yield _namespace(obj), (((obj.get("spec") or {}).get("template") or {}).get("spec")) or {}
    for cron in data.get("cronjobs") or []:
        template = (((cron.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}
        yield _namespace(cron), template.get("spec") or {}


def _annotation_secrets(ingress):
    """Secrets named by annotations such as `nginx.ingress.kubernetes.io/auth-secret: name` or `.../auth-tls-secret: ns/name`."""
    found = set()
    namespace = _namespace(ingress)
    for key, value in (_meta(ingress).get("annotations") or {}).items():
        if key.endswith("-secret") and isinstance(value, str) and value:
            ns, _, name = value.rpartition("/")
            found.add((ns or namespace, name))
    return found


def references(data):
    """({kind: {(namespace, name)}}, [(namespace, claim template, statefulset)]): everything that is named by a pod or template, an
    Ingress (TLS Secrets and annotations) or a Service Account (pull Secrets, token Secrets), and the StatefulSets' claim templates."""
    refs = {kind: set() for kind in KINDS}
    for namespace, spec in pod_specs(data):
        for kind, names in pod_references(spec).items():
            if kind in refs:
                refs[kind] |= {(namespace, name) for name in names}
    for ingress in data.get("ingresses") or []:
        namespace = _namespace(ingress)
        for tls in (ingress.get("spec") or {}).get("tls") or []:
            if tls.get("secretName"):
                refs["Secret"].add((namespace, tls["secretName"]))
        refs["Secret"] |= _annotation_secrets(ingress)
    for account in data.get("serviceaccounts") or []:
        namespace = _namespace(account)
        for field in ("imagePullSecrets", "secrets"):
            for ref in account.get(field) or []:
                if ref.get("name"):
                    refs["Secret"].add((namespace, ref["name"]))
    claims = []
    for sts in data.get("statefulsets") or []:
        for template in (sts.get("spec") or {}).get("volumeClaimTemplates") or []:
            claims.append((_namespace(sts), _meta(template).get("name", ""), _meta(sts).get("name", "")))
    return refs, claims


def _kept_by_statefulset(namespace, name, claims):
    """A claim named `<template>-<statefulset>-<ordinal>` belongs to that StatefulSet even while it is scaled down."""
    return any(ns == namespace and re.fullmatch(re.escape(f"{template}-{sts}") + r"-\d+", name) for ns, template, sts in claims)


def _system_managed(kind, meta):
    name = meta.get("name", "")
    annotations, labels = meta.get("annotations") or {}, meta.get("labels") or {}
    if kind == "ConfigMap":
        return name in ROOT_CA
    if kind == "Secret":
        return ("kubernetes.io/service-account.name" in annotations or labels.get("owner") == "helm"
                or name.startswith("sh.helm.release.v1."))
    return False


def _note(meta):
    owners = meta.get("ownerReferences") or []
    if owners:
        return f"owned by {owners[0].get('kind', '?')}/{owners[0].get('name', '?')}"
    release = (meta.get("annotations") or {}).get("meta.helm.sh/release-name")
    if release:
        return f"part of Helm release {release}"
    return "system namespace: components read objects here by name" if meta.get("namespace", "").startswith("kube-") else ""


def find_unused(data):
    """[{kind, namespace, name, created, note}] of the ConfigMaps, Secrets and claims in `data` that nothing names, sorted by kind,
    namespace and name. `data` holds lists of manifests under pods, deployments, statefulsets, daemonsets, replicasets, jobs,
    cronjobs, ingresses, serviceaccounts (what refers) and configmaps, secrets, pvcs (what is judged; metadata is enough)."""
    refs, claims = references(data)
    out = []
    for kind, key in (("ConfigMap", "configmaps"), ("Secret", "secrets"), ("PersistentVolumeClaim", "pvcs")):
        for obj in data.get(key) or []:
            meta = _meta(obj)
            name, namespace = meta.get("name", ""), meta.get("namespace", "")
            if (namespace, name) in refs[kind] or _system_managed(kind, meta):
                continue
            if kind == "PersistentVolumeClaim" and _kept_by_statefulset(namespace, name, claims):
                continue
            out.append({"kind": kind, "namespace": namespace, "name": name, "created": meta.get("creationTimestamp", ""),
                        "note": _note(meta)})
    return sorted(out, key=lambda r: (r["kind"], r["namespace"], r["name"]))


def summary(unused, judged):
    """One line: how many were judged and how many found unused, per kind. `judged` is {kind: number looked at}."""
    counts = {kind: sum(1 for r in unused if r["kind"] == kind) for kind in KINDS}
    parts = [f"{counts[kind]} of {judged.get(kind, 0)} {label}" for kind, label in
             (("ConfigMap", "ConfigMaps"), ("Secret", "Secrets"), ("PersistentVolumeClaim", "claims"))]
    return "Nothing refers to: " + ", ".join(parts) + "."
