"""Revision history and rollback of a Deployment, as `kubectl rollout history` and `kubectl rollout undo` do it. Pure functions
on the plain dicts of the API objects; no cluster access, no Qt. A Deployment's revisions are the ReplicaSets it owns: each
carries the revision number in the annotation `deployment.kubernetes.io/revision` and the pod template that revision ran."""
from copy import deepcopy

REVISION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE = "kubernetes.io/change-cause"
TEMPLATE_HASH = "pod-template-hash"  # the controller adds this label to every ReplicaSet's template; it is not part of the Deployment's


def match_selector(match_labels):
    """'app=web,tier=front' from spec.selector.matchLabels (sorted, so the request is stable)."""
    return ",".join(f"{k}={v}" for k, v in sorted((match_labels or {}).items()))


def _annotations(obj):
    return ((obj or {}).get("metadata") or {}).get("annotations") or {}


def _revision(obj):
    try:
        return int(_annotations(obj).get(REVISION, ""))
    except ValueError:
        return 0


def history(deployment, replica_sets):
    """The revisions of `deployment` (a dict), newest first: [{revision, name, created, images, replicas, change_cause,
    current}]. Only ReplicaSets the Deployment owns (by UID, so a same-named replacement's old sets are not mixed in) and that
    carry a revision number count. `current` marks the revision the Deployment is on now."""
    uid = ((deployment or {}).get("metadata") or {}).get("uid")
    now = _revision(deployment)
    rows = []
    for rs in replica_sets or []:
        meta = rs.get("metadata") or {}
        owners = meta.get("ownerReferences") or []
        revision = _revision(rs)
        if not revision or not any(o.get("uid") == uid and o.get("kind") == "Deployment" for o in owners):
            continue
        containers = (((rs.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
        rows.append({"revision": revision, "name": meta.get("name", ""), "created": meta.get("creationTimestamp", ""),
                     "images": [c.get("image", "") for c in containers if isinstance(c, dict)],
                     "replicas": (rs.get("spec") or {}).get("replicas") or 0,
                     "change_cause": _annotations(rs).get(CHANGE_CAUSE, ""), "current": revision == now})
    return sorted(rows, key=lambda r: r["revision"], reverse=True)


def rollback_template(replica_set):
    """The pod template to put back on the Deployment: the ReplicaSet's, without the controller's pod-template-hash label
    (what `kubectl rollout undo` does). A copy; the ReplicaSet dict is not touched."""
    template = deepcopy(((replica_set or {}).get("spec") or {}).get("template"))
    if not isinstance(template, dict):
        raise ValueError("This revision has no pod template to go back to.")
    labels = (template.get("metadata") or {}).get("labels")
    if isinstance(labels, dict):
        labels.pop(TEMPLATE_HASH, None)
    return template


def owned_by(replica_set, deployment):
    """True when the ReplicaSet is one of this Deployment's (same UID)."""
    uid = ((deployment or {}).get("metadata") or {}).get("uid")
    return bool(uid) and any(o.get("uid") == uid for o in ((replica_set or {}).get("metadata") or {}).get("ownerReferences") or [])
