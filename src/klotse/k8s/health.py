"""Health check over manifests, in the spirit of k9s's Popeye (pure, no Qt, no network).

`check(data)` takes what a cluster listing returned and gives findings: things that are broken now (crash loops, unbound claims,
nodes not ready) and things that are risky later (no resource limits, no probes, `latest` images). It reads manifests only and
changes nothing. Findings are advice, not verdicts: a batch pod may rightly have no probe, a single replica may be on purpose."""

ERROR, WARN, INFO = "error", "warn", "info"
SYMBOLS = {ERROR: "✖ Error", WARN: "⚠ Warning", INFO: "ℹ Note"}  # symbol and word, so colour is never the only cue
ORDER = {ERROR: 0, WARN: 1, INFO: 2}
RESTARTS_WARN = 5
PENDING_MINUTES = 15
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet")
BAD_WAITING = {"CrashLoopBackOff": ERROR, "ImagePullBackOff": ERROR, "ErrImagePull": ERROR, "CreateContainerConfigError": ERROR,
               "CreateContainerError": ERROR, "InvalidImageName": ERROR, "RunContainerError": ERROR}
PRESSURE = ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")


def finding(severity, kind, namespace, name, problem, hint=""):
    return {"severity": severity, "kind": kind, "namespace": namespace or "", "name": name, "problem": problem, "hint": hint}


def _meta(obj):
    return obj.get("metadata") or {}


def image_problem(image):
    """None, or why an image reference is not pinned: no tag (means latest) or `:latest`. A digest (`@sha256:`) is pinned."""
    if not image or "@" in image:
        return None
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return "has no tag, so it means :latest"
    if last.endswith(":latest"):
        return "uses the :latest tag"
    return None


def _containers(pod_spec):
    return list((pod_spec or {}).get("containers") or [])


def _template_spec(kind, obj):
    return ((obj.get("spec") or {}).get("template") or {}).get("spec") or {}


def container_findings(kind, obj, pod_spec):
    """Findings about the containers of a workload's pod template (or of a bare pod)."""
    meta, out = _meta(obj), []
    ns, name = meta.get("namespace", ""), meta.get("name", "")
    for c in _containers(pod_spec):
        label = f"container {c.get('name', '?')}"
        resources = c.get("resources") or {}
        if not (resources.get("limits") or {}):
            out.append(finding(WARN, kind, ns, name, f"{label} has no resource limits",
                               "Without limits it can use all of a node's CPU or memory."))
        if not (resources.get("requests") or {}):
            out.append(finding(WARN, kind, ns, name, f"{label} has no resource requests",
                               "The scheduler cannot place it well, and it is first to be evicted."))
        if not c.get("readinessProbe"):
            out.append(finding(INFO, kind, ns, name, f"{label} has no readiness probe",
                               "Traffic can reach it before it is ready."))
        if not c.get("livenessProbe"):
            out.append(finding(INFO, kind, ns, name, f"{label} has no liveness probe", "A hung process is not restarted."))
        reason = image_problem(c.get("image", ""))
        if reason:
            out.append(finding(WARN, kind, ns, name, f"{label} image {c.get('image')} {reason}",
                               "Use a fixed version so a rollout is repeatable."))
        if (c.get("securityContext") or {}).get("privileged"):
            out.append(finding(WARN, kind, ns, name, f"{label} runs privileged", "It has almost all the powers of the node."))
    return out


def pod_findings(pod):
    meta, status = _meta(pod), pod.get("status") or {}
    ns, name, out = meta.get("namespace", ""), meta.get("name", ""), []
    phase = status.get("phase", "")
    if phase == "Failed":
        out.append(finding(ERROR, "Pod", ns, name, f"failed ({status.get('reason') or 'no reason given'})",
                           status.get("message", "")[:200]))
    for group in ("initContainerStatuses", "containerStatuses"):
        for cs in status.get(group) or []:
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason", "")
            cname = cs.get("name", "?")
            if reason in BAD_WAITING:
                out.append(finding(BAD_WAITING[reason], "Pod", ns, name, f"container {cname} is {reason}",
                                   (waiting.get("message") or "")[:200]))
            terminated = (cs.get("lastState") or {}).get("terminated") or {}
            if terminated.get("reason") == "OOMKilled":
                out.append(finding(ERROR, "Pod", ns, name, f"container {cname} was OOMKilled",
                                   "It used more memory than its limit; raise the limit or fix the leak."))
            restarts = cs.get("restartCount", 0)
            if restarts >= RESTARTS_WARN and reason not in BAD_WAITING:
                out.append(finding(WARN, "Pod", ns, name, f"container {cname} restarted {restarts} times"))
    if phase == "Pending":
        for cond in status.get("conditions") or []:
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                out.append(finding(ERROR if cond.get("reason") == "Unschedulable" else WARN, "Pod", ns, name,
                                   f"cannot be scheduled ({cond.get('reason', 'pending')})", (cond.get("message") or "")[:200]))
    return out


def node_findings(node):
    name, status, out = _meta(node).get("name", ""), node.get("status") or {}, []
    conditions = {c.get("type"): c for c in status.get("conditions") or []}
    ready = conditions.get("Ready")
    if ready is None or ready.get("status") != "True":
        out.append(finding(ERROR, "Node", "", name, "is not Ready", (ready or {}).get("message", "")[:200]))
    for kind in PRESSURE:
        if (conditions.get(kind) or {}).get("status") == "True":
            out.append(finding(ERROR if kind != "NetworkUnavailable" else WARN, "Node", "", name, f"reports {kind}",
                               (conditions[kind].get("message") or "")[:200]))
    if (node.get("spec") or {}).get("unschedulable"):
        out.append(finding(INFO, "Node", "", name, "is cordoned (no new pods are placed on it)"))
    return out


def workload_findings(kind, obj):
    meta, spec, status = _meta(obj), obj.get("spec") or {}, obj.get("status") or {}
    ns, name = meta.get("namespace", ""), meta.get("name", "")
    out = container_findings(kind, obj, _template_spec(kind, obj))
    if kind in ("Deployment", "StatefulSet"):
        wanted = spec.get("replicas", 1)
        ready = status.get("readyReplicas", 0) or 0
        if wanted and ready == 0:
            out.append(finding(ERROR, kind, ns, name, f"has none of its {wanted} replicas ready"))
        elif wanted and ready < wanted:
            out.append(finding(WARN, kind, ns, name, f"has {ready} of {wanted} replicas ready"))
        elif wanted == 1:
            out.append(finding(INFO, kind, ns, name, "runs a single replica", "It is unavailable during restarts and node loss."))
    if kind == "DaemonSet":
        desired, ready = status.get("desiredNumberScheduled", 0) or 0, status.get("numberReady", 0) or 0
        if desired and ready < desired:
            out.append(finding(ERROR if ready == 0 else WARN, kind, ns, name, f"has {ready} of {desired} pods ready"))
    return out


def claim_findings(pvc):
    meta, phase = _meta(pvc), (pvc.get("status") or {}).get("phase", "")
    ns, name = meta.get("namespace", ""), meta.get("name", "")
    if phase == "Pending":
        return [finding(ERROR, "PersistentVolumeClaim", ns, name, "is not bound to a volume",
                        "Check the storage class and whether a volume or provisioner is available.")]
    if phase == "Lost":
        return [finding(ERROR, "PersistentVolumeClaim", ns, name, "lost its volume")]
    return []


def service_findings(service, pods):
    meta, spec = _meta(service), service.get("spec") or {}
    ns, name = meta.get("namespace", ""), meta.get("name", "")
    selector = spec.get("selector")
    if not selector or spec.get("type") == "ExternalName":
        return []
    ready = 0
    for pod in pods:
        labels = _meta(pod).get("labels") or {}
        if _meta(pod).get("namespace") == ns and all(labels.get(k) == v for k, v in selector.items()):
            conditions = {c.get("type"): c.get("status") for c in (pod.get("status") or {}).get("conditions") or []}
            ready += conditions.get("Ready") == "True"
    if ready == 0:
        return [finding(WARN, "Service", ns, name, "selects no ready pod", "Its selector may not match any pod, or none is ready.")]
    return []


def check(data):
    """Findings for `data`, a dict of manifest lists: pods, nodes, deployments, statefulsets, daemonsets, pvcs, services (any
    may be missing). Sorted: errors first, then by kind, namespace and name."""
    out = []
    pods = data.get("pods") or []
    for pod in pods:
        out += pod_findings(pod)
        if not any(o.get("controller") for o in _meta(pod).get("ownerReferences") or []):
            out += container_findings("Pod", pod, pod.get("spec"))  # a bare pod has no workload to speak for it
    for node in data.get("nodes") or []:
        out += node_findings(node)
    for key, kind in (("deployments", "Deployment"), ("statefulsets", "StatefulSet"), ("daemonsets", "DaemonSet")):
        for obj in data.get(key) or []:
            out += workload_findings(kind, obj)
    for pvc in data.get("pvcs") or []:
        out += claim_findings(pvc)
    for service in data.get("services") or []:
        out += service_findings(service, pods)
    return sorted(out, key=lambda f: (ORDER[f["severity"]], f["kind"], f["namespace"], f["name"], f["problem"]))


def summary(findings):
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in (ERROR, WARN, INFO)}
    return counts, (f"{counts[ERROR]} error{'s' if counts[ERROR] != 1 else ''}, {counts[WARN]} warning{'s' if counts[WARN] != 1 else ''}, "
                    f"{counts[INFO]} note{'s' if counts[INFO] != 1 else ''}")
