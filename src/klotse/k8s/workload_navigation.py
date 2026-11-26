"""Immediate workload relationships from Kubernetes owner references."""
import re
from typing import NamedTuple


CHILD_KIND = {
    "Deployment": "ReplicaSet", "ReplicaSet": "Pod", "DaemonSet": "Pod",
    "StatefulSet": "Pod", "Job": "Pod", "CronJob": "Job",
}
_OWNER_GROUP = {
    "Deployment": "apps", "ReplicaSet": "apps", "DaemonSet": "apps",
    "StatefulSet": "apps", "Job": "batch", "CronJob": "batch",
}
_RESOURCE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*")


class Identity(NamedTuple):
    kind: str
    namespace: str
    name: str
    uid: str


def _identity(kind, namespace, name, uid):
    if (isinstance(namespace, str) and len(namespace) <= 63 and "." not in namespace
            and _RESOURCE_NAME.fullmatch(namespace)
            and isinstance(name, str) and len(name) <= 253 and _RESOURCE_NAME.fullmatch(name)
            and isinstance(uid, str) and uid):
        return Identity(kind, namespace, name, uid)
    return None


def _metadata(raw):
    return raw.get("metadata") or {}


def identity(kind, raw):
    meta = _metadata(raw)
    return _identity(kind, meta.get("namespace"), meta.get("name"), meta.get("uid"))


def _owners(child_kind, raw):
    meta = _metadata(raw)
    for ref in meta.get("ownerReferences") or []:
        kind = ref.get("kind")
        version = ref.get("apiVersion")
        if (CHILD_KIND.get(kind) == child_kind and isinstance(version, str)
                and version.partition("/")[0] == _OWNER_GROUP[kind] and "/" in version):
            owner = _identity(kind, meta.get("namespace"), ref.get("name"), ref.get("uid"))
            if owner is not None:
                yield owner, ref.get("controller") is True


def controller(child_kind, raw):
    return next((owner for owner, controls in _owners(child_kind, raw) if controls), None)


def directly_owned(child_kind, raw, owner):
    return any(ref == owner for ref, _ in _owners(child_kind, raw))
