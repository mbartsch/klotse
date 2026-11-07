"""What `kubectl drain` would do with the pods on a node, worked out from their manifests (pure, no Qt, no network).

The rules follow kubectl's default drain filters: finished pods are deleted, mirror (static) pods are skipped, DaemonSet pods
are skipped with --ignore-daemonsets and otherwise stop the drain, pods without a controller stop it unless --force, pods with
an emptyDir volume stop it unless --delete-emptydir-data. A PodDisruptionBudget that matches a pod and allows too few
disruptions makes the eviction wait (and fail at the timeout)."""

EVICT, DELETE, SKIP, STOP = "evict", "delete", "skip", "stop"  # what happens to a pod; STOP = kubectl refuses to drain


def _meta(obj):
    return obj.get("metadata") or {}


def _controller(pod):
    for owner in _meta(pod).get("ownerReferences") or []:
        if owner.get("controller"):
            return owner
    return None


def selector_matches(selector, labels):
    """A LabelSelector dict against a labels dict. An empty selector matches everything, a missing one nothing."""
    if selector is None:
        return False
    labels = labels or {}
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr.get("key"), expr.get("operator"), expr.get("values") or []
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and key in labels and labels[key] in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def classify(pod, ignore_daemonsets, delete_emptydir, force):
    """(action, reason) for one pod."""
    status = (pod.get("status") or {}).get("phase")
    if status in ("Succeeded", "Failed"):
        return DELETE, f"already finished ({status})"
    if "kubernetes.io/config.mirror" in (_meta(pod).get("annotations") or {}):
        return SKIP, "static (mirror) pod, cannot be evicted"
    owner = _controller(pod)
    if owner and owner.get("kind") == "DaemonSet":
        return (SKIP, "DaemonSet pod, left running") if ignore_daemonsets else (STOP, "DaemonSet pod: tick Skip DaemonSet pods")
    if owner is None and not force:
        return STOP, "no controller: it would not come back (needs --force)"
    if not delete_emptydir and any("emptyDir" in v for v in (pod.get("spec") or {}).get("volumes") or []):
        return STOP, "uses an emptyDir volume (needs Allow deletion of emptyDir data)"
    return EVICT, "evicted" + ("" if owner else " (no controller, will not come back)")


def plan(pods, pdbs, ignore_daemonsets=True, delete_emptydir=False, force=False):
    """One entry per pod: {"namespace", "name", "action", "reason", "pdb"}. `pdb` is None, or a sentence naming a budget that
    matches the pod and (allowed disruptions in total) is too small for the pods being evicted under it."""
    entries, per_budget = [], {}
    for pod in pods:
        action, reason = classify(pod, ignore_daemonsets, delete_emptydir, force)
        meta = _meta(pod)
        entry = {"namespace": meta.get("namespace", ""), "name": meta.get("name", ""), "action": action, "reason": reason, "pdb": None}
        entries.append(entry)
        if action != EVICT:
            continue
        for budget in pdbs:
            bmeta = _meta(budget)
            spec = budget.get("spec") or {}
            if bmeta.get("namespace") == entry["namespace"] and selector_matches(spec.get("selector"), meta.get("labels")):
                per_budget.setdefault((bmeta.get("namespace"), bmeta.get("name")), (budget, []))[1].append(entry)
    for (_, name), (budget, members) in per_budget.items():
        allowed = (budget.get("status") or {}).get("disruptionsAllowed") or 0
        if len(members) > allowed:
            text = (f"PodDisruptionBudget {name} allows {allowed} disruption{'s' if allowed != 1 else ''} "
                    f"for {len(members)} pod{'s' if len(members) != 1 else ''} being evicted")
            for entry in members:
                entry["pdb"] = text
    return entries


def summary(entries):
    """A one-paragraph verdict for the top of the preview."""
    counts = {a: sum(1 for e in entries if e["action"] == a) for a in (EVICT, DELETE, SKIP, STOP)}
    blocked_by_budget = sorted({e["pdb"] for e in entries if e["pdb"]})
    parts = [f"{counts[EVICT]} to evict", f"{counts[SKIP]} skipped", f"{counts[DELETE]} finished pods removed"]
    text = ", ".join(parts) + "."
    if counts[STOP]:
        text += f" kubectl would refuse to drain: {counts[STOP]} pod{'s' if counts[STOP] != 1 else ''} need an option."
    if blocked_by_budget:
        text += " Budgets that may hold the drain up: " + "; ".join(blocked_by_budget) + "."
    return text
