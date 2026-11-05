"""Readable (HTML) rendering of workloads for the Details panel. Pure functions on the manifest dict, no Qt.

Covers Deployment, StatefulSet, DaemonSet, ReplicaSet, Pod, Job and CronJob. The page is laid out from what a person
checks first: a status line, the key facts, the conditions, then one card per container (image, command, ports, resources,
probes, environment, mounts), the volumes and scheduling rules, and last the labels and annotations. Facts are shown as
label/value rows rather than wide tables, so the page stays readable in a narrow dock. Values from the cluster are escaped.
Environment values from a Secret are never read: only the reference (secret name and key) is shown."""
from . import theme
from .rbac_view import _e, _pairs, _section, _table, _when
from .status_tone import status_tone

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job", "CronJob")
LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
LONG_VALUE = 240  # a longer annotation or variable value is cut in the page (the YAML view has all of it)


def kind_of(raw):
    """The workload kind of a manifest (the list API leaves `kind` out, so it is read from the spec), or None."""
    if not isinstance(raw, dict):
        return None
    if raw.get("kind") in WORKLOAD_KINDS:
        return raw["kind"]
    spec = raw.get("spec")
    if not isinstance(spec, dict):
        return None
    if "jobTemplate" in spec:
        return "CronJob"
    if "template" not in spec:
        return "Pod" if isinstance(spec.get("containers"), list) else None
    if "serviceName" in spec:
        return "StatefulSet"
    if "strategy" in spec or "progressDeadlineSeconds" in spec:
        return "Deployment"
    if "updateStrategy" in spec:
        return "DaemonSet"
    if any(k in spec for k in ("completions", "backoffLimit", "completionMode", "ttlSecondsAfterFinished")):
        return "Job"
    return "ReplicaSet" if "replicas" in spec else None


# ---- small pieces ----------------------------------------------------------------------------------------------
def _muted(text):
    return f"<span style='color:{theme.MUTED}'>{_e(text)}</span>"


def _cut(value):
    text = str(value)
    return text if len(text) <= LONG_VALUE else text[:LONG_VALUE] + "…"


def _tone_color(tone):
    return {"ok": theme.OK, "warn": theme.WARN, "bad": theme.BAD}.get(tone, theme.MUTED)


def badge(text, tone=None):
    """A status chip: a symbol and the words, so the state never depends on colour alone."""
    symbol = {"ok": "✔", "warn": "▲", "bad": "✖"}.get(tone, "●")
    return f"<b style='color:{_tone_color(tone)}'>{symbol} {_e(text)}</b>"


def _facts(rows):
    """Label/value rows; rows with an empty value are left out."""
    rows = [(k, v) for k, v in rows if v not in (None, "")]
    if not rows:
        return ""
    body = "".join(f"<tr><td valign='top' style='color:{theme.MUTED}; padding:2px 16px 2px 0' nowrap>{_e(k)}</td>"
                   f"<td style='padding:2px 0'>{v}</td></tr>" for k, v in rows)
    return f"<table cellspacing='0'>{body}</table>"


def _count(spec_value, status_value):
    return f"{status_value or 0} of {spec_value or 0}"


def _ready_tone(ready, wanted):
    if not wanted:
        return None
    return "ok" if ready >= wanted else ("warn" if ready else "bad")


def _owner(meta):
    owners = meta.get("ownerReferences") or []
    return ", ".join(f"{_e(o.get('kind', ''))} <b>{_e(o.get('name', ''))}</b>" for o in owners)


def _selector(selector):
    if not isinstance(selector, dict):
        return ""
    parts = [f"{_e(k)}={_e(v)}" for k, v in sorted((selector.get("matchLabels") or {}).items())]
    parts += [f"{_e(e.get('key'))} {_e(e.get('operator'))} ({_e(','.join(e.get('values') or []))})"
              for e in selector.get("matchExpressions") or []]
    return "<br>".join(parts)


def _selected_dict(mapping):
    return "<br>".join(f"{_e(k)}={_e(v)}" for k, v in sorted(mapping.items())) if mapping else ""


# ---- header and status -----------------------------------------------------------------------------------------
def status_line(kind, raw):
    """(text, tone) of the workload's state, for the badge under the title."""
    spec, status = raw.get("spec") or {}, raw.get("status") or {}
    meta = raw.get("metadata") or {}
    if kind == "Pod":
        if meta.get("deletionTimestamp"):
            return "Terminating", "warn"
        phase = status.get("phase") or "Unknown"
        for cs in (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or []):
            waiting = (cs.get("state") or {}).get("waiting") or {}
            if waiting.get("reason") and waiting["reason"] not in ("PodInitializing", "ContainerCreating"):
                return waiting["reason"], "bad"
        return phase, status_tone(phase)
    if kind == "DaemonSet":
        wanted, ready = status.get("desiredNumberScheduled") or 0, status.get("numberReady") or 0
        return (f"{ready} of {wanted} ready" if wanted else "No nodes selected"), _ready_tone(ready, wanted)
    if kind == "Job":
        conds = {c.get("type"): c for c in status.get("conditions") or [] if c.get("status") == "True"}
        if "Failed" in conds:
            return "Failed", "bad"
        if "Complete" in conds:
            return "Complete", "ok"
        if spec.get("suspend"):
            return "Suspended", "warn"
        return f"Running ({status.get('active') or 0} active)", "warn"
    if kind == "CronJob":
        if spec.get("suspend"):
            return "Suspended", "warn"
        active = len(status.get("active") or [])
        return (f"{active} running" if active else "Scheduled"), "ok"
    wanted = spec.get("replicas")
    wanted = 1 if wanted is None else wanted
    ready = status.get("readyReplicas") or 0
    if not wanted:
        return "Scaled to zero", None
    return f"{ready} of {wanted} ready", _ready_tone(ready, wanted)


def _header(kind, raw):
    meta = raw.get("metadata") or {}
    text, tone = status_line(kind, raw)
    title = (f"<h2 style='margin:0'>{_e(kind)} <span style='color:{theme.ACCENT_HI}'>{_e(meta.get('name', ''))}</span></h2>")
    facts = []
    if meta.get("namespace"):
        facts.append(("Namespace", meta["namespace"]))
    if meta.get("creationTimestamp"):
        facts.append(("Created", _when(meta["creationTimestamp"])))
    line = " &nbsp;·&nbsp; ".join(f"<span style='color:{theme.MUTED}'>{_e(k)}</span> {_e(v)}" for k, v in facts)
    return title + f"<p style='margin:6px 0 0 0'>{badge(text, tone)}</p>" + (f"<p style='margin:4px 0 0 0'>{line}</p>" if line else "")


# ---- summary per kind ------------------------------------------------------------------------------------------
def _template(kind, raw):
    spec = raw.get("spec") or {}
    if kind == "Pod":
        return raw
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template")) or {}
    return spec.get("template") or {}


def _pod_spec(kind, raw):
    if kind == "Pod":
        return raw.get("spec") or {}
    return (_template(kind, raw).get("spec")) or {}


def _rollout(kind, raw):
    spec = raw.get("spec") or {}
    if kind == "Deployment":
        strategy = spec.get("strategy") or {}
        rolling = strategy.get("rollingUpdate") or {}
        text = strategy.get("type", "RollingUpdate")
        if rolling:
            text += f" (max surge {_e(rolling.get('maxSurge', '–'))}, max unavailable {_e(rolling.get('maxUnavailable', '–'))})"
        return text
    if kind in ("StatefulSet", "DaemonSet"):
        strategy = spec.get("updateStrategy") or {}
        rolling = strategy.get("rollingUpdate") or {}
        text = strategy.get("type", "RollingUpdate")
        extras = [f"{k} {_e(v)}" for k, v in (("partition", rolling.get("partition")),
                                              ("max unavailable", rolling.get("maxUnavailable"))) if v is not None]
        return text + (f" ({', '.join(extras)})" if extras else "")
    return ""


def _summary(kind, raw):
    spec, status = raw.get("spec") or {}, raw.get("status") or {}
    meta = raw.get("metadata") or {}
    pod = _pod_spec(kind, raw)
    rows = [("Owner", _owner(meta))]
    if kind in ("Deployment", "StatefulSet", "ReplicaSet"):
        wanted = spec.get("replicas")
        wanted = 1 if wanted is None else wanted
        rows += [("Replicas", f"<b>{_e(wanted)}</b> wanted &nbsp;·&nbsp; {_e(status.get('readyReplicas') or 0)} ready"
                              f" &nbsp;·&nbsp; {_e(status.get('updatedReplicas') or 0)} updated"
                              + (f" &nbsp;·&nbsp; {_e(status.get('availableReplicas') or 0)} available" if kind != "StatefulSet" else "")),
                 ("Update strategy", _rollout(kind, raw)),
                 ("Selector", _selector(spec.get("selector")))]
        if kind == "StatefulSet":
            rows += [("Service", _e(spec.get("serviceName", ""))), ("Pod management", _e(spec.get("podManagementPolicy", "")))]
        if kind == "Deployment":
            rows += [("Paused", "yes" if spec.get("paused") else ""),
                     ("Progress deadline", f"{_e(spec['progressDeadlineSeconds'])} s" if spec.get("progressDeadlineSeconds") else "")]
        rows += [("Min ready", f"{_e(spec['minReadySeconds'])} s" if spec.get("minReadySeconds") else ""),
                 ("Revisions kept", _e(spec.get("revisionHistoryLimit", "")))]
    elif kind == "DaemonSet":
        rows += [("Nodes", f"<b>{_e(status.get('desiredNumberScheduled') or 0)}</b> wanted &nbsp;·&nbsp; "
                           f"{_e(status.get('numberReady') or 0)} ready &nbsp;·&nbsp; {_e(status.get('updatedNumberScheduled') or 0)} updated"
                           f" &nbsp;·&nbsp; {_e(status.get('numberMisscheduled') or 0)} misscheduled"),
                 ("Update strategy", _rollout(kind, raw)), ("Selector", _selector(spec.get("selector")))]
    elif kind == "Pod":
        rows += [("Node", _e(pod.get("nodeName", ""))), ("Pod IP", _e(status.get("podIP", ""))),
                 ("Host IP", _e(status.get("hostIP", ""))), ("QoS class", _e(status.get("qosClass", ""))),
                 ("Started", _when(status["startTime"]) if status.get("startTime") else ""),
                 ("Status message", _e(status.get("message", "")) or _e(status.get("reason", "")))]
    elif kind == "Job":
        rows += [("Completions", f"{_e(status.get('succeeded') or 0)} done of {_e(spec.get('completions', 1))}"),
                 ("Parallelism", _e(spec.get("parallelism", 1))),
                 ("Active / failed", f"{_e(status.get('active') or 0)} / {_e(status.get('failed') or 0)}"),
                 ("Retries allowed", _e(spec.get("backoffLimit", 6))),
                 ("Deadline", f"{_e(spec['activeDeadlineSeconds'])} s" if spec.get("activeDeadlineSeconds") else ""),
                 ("Delete when finished", f"after {_e(spec['ttlSecondsAfterFinished'])} s" if spec.get("ttlSecondsAfterFinished") is not None else ""),
                 ("Started", _when(status["startTime"]) if status.get("startTime") else ""),
                 ("Finished", _when(status["completionTime"]) if status.get("completionTime") else "")]
    elif kind == "CronJob":
        job = (spec.get("jobTemplate") or {}).get("spec") or {}
        rows += [("Schedule", f"<code>{_e(spec.get('schedule', ''))}</code>" +
                  (f" &nbsp;{_muted(spec['timeZone'])}" if spec.get("timeZone") else "")),
                 ("Concurrency", _e(spec.get("concurrencyPolicy", "Allow"))),
                 ("Last run", _when(status["lastScheduleTime"]) if status.get("lastScheduleTime") else "never"),
                 ("Last success", _when(status["lastSuccessfulTime"]) if status.get("lastSuccessfulTime") else ""),
                 ("History kept", f"{_e(spec.get('successfulJobsHistoryLimit', 3))} successful, {_e(spec.get('failedJobsHistoryLimit', 1))} failed"),
                 ("Start deadline", f"{_e(spec['startingDeadlineSeconds'])} s" if spec.get("startingDeadlineSeconds") else ""),
                 ("Job retries", _e(job.get("backoffLimit", 6))), ("Job parallelism", _e(job.get("parallelism", 1)))]
    rows += [("Service account", _e(pod.get("serviceAccountName", ""))),
             ("Restart policy", _e(pod.get("restartPolicy", ""))),
             ("Priority class", _e(pod.get("priorityClassName", ""))),
             ("Node selector", _selected_dict(pod.get("nodeSelector")))]
    return _section("Summary", _facts(rows) or _muted("Nothing to show."))


def _conditions(raw):
    conds = (raw.get("status") or {}).get("conditions") or []
    if not conds:
        return ""
    rows = []
    for c in conds:
        good_when_true = c.get("type") not in ("Failed", "FailureTarget", "ReplicaFailure", "DiskPressure", "MemoryPressure",
                                               "PIDPressure", "NetworkUnavailable", "Stalled")
        ok = (c.get("status") == "True") == good_when_true
        state = badge(c.get("status", ""), "ok" if ok else "warn") if c.get("status") in ("True", "False") else _e(c.get("status", ""))
        message = _cut(c.get("message") or "")
        rows.append((f"<b>{_e(c.get('type', ''))}</b>", state, _e(c.get("reason") or "–"),
                     _e(message) or "–", _when(c["lastTransitionTime"]) if c.get("lastTransitionTime") else "–"))
    return _section("Conditions", _table(("Type", "Status", "Reason", "Message", "Since"), rows))


# ---- containers ------------------------------------------------------------------------------------------------
def _probe_text(probe):
    if not probe:
        return ""
    if probe.get("httpGet"):
        h = probe["httpGet"]
        what = f"HTTP GET {h.get('scheme', 'HTTP').lower()}://:{h.get('port', '')}{h.get('path', '/')}"
    elif probe.get("tcpSocket"):
        what = f"TCP :{probe['tcpSocket'].get('port', '')}"
    elif probe.get("exec"):
        what = "exec " + " ".join(probe["exec"].get("command") or [])
    elif probe.get("grpc"):
        what = f"gRPC :{probe['grpc'].get('port', '')}"
    else:
        what = "custom"
    timing = ", ".join(f"{label} {probe[k]}" for k, label in (("initialDelaySeconds", "delay"), ("periodSeconds", "every"),
                                                              ("timeoutSeconds", "timeout"), ("failureThreshold", "fail after"))
                       if probe.get(k) is not None)
    return f"{_e(what)}" + (f" {_muted('(' + timing + ')')}" if timing else "")


def _resources(container):
    res = container.get("resources") or {}
    parts = []
    for label, key in (("requests", "requests"), ("limits", "limits")):
        values = res.get(key) or {}
        if values:
            parts.append(f"{label}: " + ", ".join(f"{_e(k)} {_e(v)}" for k, v in sorted(values.items())))
    return "<br>".join(parts) if parts else _muted("none set")


def _env(container):
    lines = []
    for var in container.get("env") or []:
        name, source = _e(var.get("name", "")), var.get("valueFrom") or {}
        if "value" in var or not source:
            lines.append(f"<code>{name}</code> = {_e(_cut(var.get('value', '')))}")
        elif source.get("secretKeyRef"):
            ref = source["secretKeyRef"]
            lines.append(f"<code>{name}</code> {_muted('←')} secret {_e(ref.get('name', ''))} / {_e(ref.get('key', ''))}")
        elif source.get("configMapKeyRef"):
            ref = source["configMapKeyRef"]
            lines.append(f"<code>{name}</code> {_muted('←')} config map {_e(ref.get('name', ''))} / {_e(ref.get('key', ''))}")
        elif source.get("fieldRef"):
            lines.append(f"<code>{name}</code> {_muted('←')} field {_e(source['fieldRef'].get('fieldPath', ''))}")
        elif source.get("resourceFieldRef"):
            lines.append(f"<code>{name}</code> {_muted('←')} resource {_e(source['resourceFieldRef'].get('resource', ''))}")
        else:
            lines.append(f"<code>{name}</code>")
    for ref in container.get("envFrom") or []:
        kind, body = ("config map", ref.get("configMapRef")) if ref.get("configMapRef") else ("secret", ref.get("secretRef"))
        if body:
            prefix = f" with prefix {_e(ref['prefix'])}" if ref.get("prefix") else ""
            lines.append(f"all keys from {kind} <b>{_e(body.get('name', ''))}</b>{prefix}")
    return "<br>".join(lines)


def _ports(container):
    return ", ".join(f"{_e(p.get('containerPort', ''))}/{_e(p.get('protocol', 'TCP'))}" + (f" ({_e(p['name'])})" if p.get("name") else "")
                     for p in container.get("ports") or [])


def _state(status):
    """(text, tone) of a container status."""
    state = status.get("state") or {}
    if "running" in state:
        return "Running", "ok"
    if "waiting" in state:
        reason = state["waiting"].get("reason") or "Waiting"
        return reason, ("warn" if reason in ("PodInitializing", "ContainerCreating") else "bad")
    if "terminated" in state:
        done = state["terminated"]
        reason = done.get("reason") or "Terminated"
        return f"{reason} (exit {done.get('exitCode', '?')})", ("ok" if done.get("exitCode") == 0 else "bad")
    return "Unknown", None


def _container_card(container, status, init):
    name = container.get("name", "")
    title = f"<b>{_e(name)}</b>" + (f" {_muted('init container')}" if init else "")
    rows = []
    if status:
        text, tone = _state(status)
        restarts = status.get("restartCount") or 0
        rows.append(("State", badge(text, tone) + f" &nbsp; {'ready' if status.get('ready') else 'not ready'}"
                     + (f" &nbsp;·&nbsp; {restarts} restart{'s' if restarts != 1 else ''}" if restarts else "")))
        last = (status.get("lastState") or {}).get("terminated")
        if last:
            rows.append(("Last exit", f"{_e(last.get('reason', ''))} (exit {_e(last.get('exitCode', '?'))})"))
    rows += [("Image", f"<code>{_e(container.get('image', ''))}</code>"),
             ("Command", f"<code>{_e(' '.join(container.get('command') or []))}</code>" if container.get("command") else ""),
             ("Arguments", f"<code>{_e(' '.join(container.get('args') or []))}</code>" if container.get("args") else ""),
             ("Ports", _ports(container)), ("Resources", _resources(container)),
             ("Liveness", _probe_text(container.get("livenessProbe"))), ("Readiness", _probe_text(container.get("readinessProbe"))),
             ("Startup", _probe_text(container.get("startupProbe"))), ("Environment", _env(container)),
             ("Mounts", "<br>".join(f"{_e(m.get('name', ''))} {_muted('→')} <code>{_e(m.get('mountPath', ''))}</code>"
                                    + (" (read-only)" if m.get("readOnly") else "") for m in container.get("volumeMounts") or []))]
    if (container.get("securityContext") or {}).get("privileged"):
        rows.append(("Security", f"<b style='color:{theme.WARN}'>▲ privileged</b>"))
    return f"<p style='margin:10px 0 2px 0'>{title}</p>" + _facts(rows)


def _containers(kind, raw):
    pod = _pod_spec(kind, raw)
    status = raw.get("status") or {} if kind == "Pod" else {}
    by_name = {s.get("name"): s for s in (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or [])}
    cards = [_container_card(c, by_name.get(c.get("name")), True) for c in pod.get("initContainers") or []]
    cards += [_container_card(c, by_name.get(c.get("name")), False) for c in pod.get("containers") or []]
    return _section(f"Containers ({len(cards)})", "".join(cards)) if cards else ""


# ---- pod-level settings --------------------------------------------------------------------------------------------
def _volume_source(volume):
    for key, value in volume.items():
        if key == "name":
            continue
        detail = ""
        if isinstance(value, dict):
            detail = value.get("name") or value.get("claimName") or value.get("secretName") or value.get("path") or ""
        return f"{_e(key)}" + (f" {_muted(_e(detail))}" if detail else "")
    return ""


def _default_toleration(t):
    """The two NoExecute tolerations (not-ready, unreachable; 300 s) that Kubernetes adds to every pod: not worth a row."""
    group = str(t.get("key", "")).partition("/")[0]  # a taint key's prefix, not a web address
    return t.get("effect") == "NoExecute" and group == "node.kubernetes.io" and t.get("tolerationSeconds") == 300


def _scheduling(kind, raw):
    pod = _pod_spec(kind, raw)
    out = ""
    volumes = pod.get("volumes") or []
    if volumes:
        out += _section(f"Volumes ({len(volumes)})", _table(("Name", "Source"), [(f"<b>{_e(v.get('name', ''))}</b>", _volume_source(v))
                                                                               for v in volumes]))
    tolerations = pod.get("tolerations") or []
    visible = [t for t in tolerations if not _default_toleration(t)]
    if visible:
        rows = [(_e(t.get("key", "") or "(all)"), _e(t.get("operator", "Equal")), _e(t.get("value", "") or "–"),
                 _e(t.get("effect", "") or "any")) for t in visible]
        out += _section(f"Tolerations ({len(visible)})", _table(("Key", "Operator", "Value", "Effect"), rows))
    flags = [name for name, key in (("Affinity rules", "affinity"), ("Topology spread", "topologySpreadConstraints")) if pod.get(key)]
    if flags:
        out += _section("Placement", f"{_e(', '.join(flags))} {_muted('(see the YAML view for the details)')}")
    pulls = [s.get("name", "") for s in pod.get("imagePullSecrets") or []]
    if pulls:
        out += _section("Image pull secrets", _e(", ".join(pulls)))
    return out


def _metadata(raw):
    meta = raw.get("metadata") or {}
    notes = {k: _cut(v) for k, v in (meta.get("annotations") or {}).items() if k != LAST_APPLIED}
    return _section("Labels", _pairs(meta.get("labels"))) + _section("Annotations", _pairs(notes))


def render(kind, raw):
    """HTML for `kind` (one of WORKLOAD_KINDS) built from its manifest dict."""
    if kind not in WORKLOAD_KINDS or not isinstance(raw, dict):
        raise ValueError(f"No formatted view for {kind}")
    body = (_header(kind, raw) + _summary(kind, raw) + _conditions(raw) + _containers(kind, raw) + _scheduling(kind, raw)
            + _metadata(raw))
    return f"<div style='font-size:13px'>{body}</div>"  # no <hr>: Qt draws it white, whatever the theme

