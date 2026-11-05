"""The data side of the workload editor (no Qt): validating what was typed and building the new manifest.

`values` is what the dialog collects (see `WorkloadEditDialog.values`):

    replicas, strategy_type, knobs {key: text}, min_ready, history, progress_deadline (texts), paused (None/True/False),
    labels, annotations {dict} and *_pairs [(key, value)],
    pod: service_account, priority_class, grace (text), node_selector {dict}, tolerations [dict], pull_secrets [name],
         volumes [dict], template_labels, template_annotations {dict} and *_pairs, security {runAsUser, runAsGroup,
         fsGroup (text), runAsNonRoot (None/True/False)},
    containers: [{name, field, index, image, pull, requests, limits, env [dict], env_from [dict], command [str],
                  args [str], ports [dict], mounts [dict], probes {liveness, readiness, startup: probe values},
                  security {runAsUser, runAsGroup (text), runAsNonRoot, readOnlyRootFilesystem,
                  allowPrivilegeEscalation, privileged (None/True/False)}}]

Anything the form does not know is copied from the original manifest."""
import re
from copy import deepcopy

from .service_account_editor import validate_pairs

WORKLOAD_KINDS = ("Deployment", "DaemonSet", "StatefulSet")
HAS_REPLICAS = ("Deployment", "StatefulSet")
PULL_POLICIES = ("", "Always", "IfNotPresent", "Never")  # "" = not set (Kubernetes picks from the image tag)
# kind -> (spec field, strategy types, [(rollingUpdate key, label, kind of number)])
STRATEGIES = {
    "Deployment": ("strategy", ("RollingUpdate", "Recreate"),
                   [("maxSurge", "Max surge", "count_or_percent"), ("maxUnavailable", "Max unavailable", "count_or_percent")]),
    "DaemonSet": ("updateStrategy", ("RollingUpdate", "OnDelete"),
                  [("maxUnavailable", "Max unavailable", "count_or_percent")]),
    "StatefulSet": ("updateStrategy", ("RollingUpdate", "OnDelete"),
                    [("partition", "Partition (pods with a lower number keep the old version)", "count")]),
}
QUANTITY = re.compile(r"(\d+(\.\d+)?|\.\d+)(n|u|m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?")
ENV_NAME = re.compile(r"[-._a-zA-Z][-._a-zA-Z0-9]*")
DNS_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
SCALE = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
         "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60}
RESOURCES = (("cpu", "CPU", "e.g. 250m or 0.5"), ("memory", "Memory", "e.g. 256Mi or 1Gi"))
PROTOCOLS = ("TCP", "UDP", "SCTP")
TOLERATION_OPERATORS = ("Equal", "Exists")
TAINT_EFFECTS = ("NoSchedule", "PreferNoSchedule", "NoExecute")
PROBE_NAMES = (("livenessProbe", "Liveness"), ("readinessProbe", "Readiness"), ("startupProbe", "Startup"))
PROBE_MODES = ("None", "HTTP GET", "TCP socket", "Command", "gRPC")
PROBE_HANDLER = {"HTTP GET": "httpGet", "TCP socket": "tcpSocket", "Command": "exec", "gRPC": "grpc"}
PROBE_TIMINGS = (("initialDelaySeconds", "Initial delay (s)"), ("periodSeconds", "Period (s)"),
                 ("timeoutSeconds", "Timeout (s)"), ("successThreshold", "Success threshold"),
                 ("failureThreshold", "Failure threshold"))
VOLUME_TYPES = ("emptyDir", "configMap", "secret", "persistentVolumeClaim", "hostPath")
VOLUME_KEYS = {"configMap": "name", "secret": "secretName", "persistentVolumeClaim": "claimName", "hostPath": "path"}
OTHER_VOLUME = "(other)"
CONTAINER_IDS = (("runAsUser", "Run as user ID"), ("runAsGroup", "Run as group ID"))
CONTAINER_FLAGS = (("runAsNonRoot", "Must run as non-root"), ("readOnlyRootFilesystem", "Read-only root filesystem"),
                   ("allowPrivilegeEscalation", "Allow privilege escalation"), ("privileged", "Privileged"))


# ---- small parsers ------------------------------------------------------------------
def valid_quantity(text):
    return QUANTITY.fullmatch(text.strip()) is not None


def quantity_value(text):
    """A CPU or memory quantity as a number (cores or bytes), or None when it is not valid."""
    match = QUANTITY.fullmatch(text.strip())
    return None if not match else float(match.group(1)) * SCALE[match.group(3) or ""]


def valid_count(text, percent=False):
    """A whole number, or with `percent` also a percentage such as 25%."""
    return re.fullmatch(r"\d+%?" if percent else r"\d+", str(text).strip()) is not None


def number_or_text(text):
    """'2' -> 2 and '25%' -> '25%': how Kubernetes stores an int-or-percent (and a port that may be a name)."""
    text = str(text).strip()
    return int(text) if text.isdigit() else text


def env_source(entry):
    """What a variable that has no plain value comes from, in words."""
    ref = entry.get("valueFrom") or {}
    for key, label in (("secretKeyRef", "Secret"), ("configMapKeyRef", "ConfigMap")):
        if key in ref:
            return f"{label} {ref[key].get('name', '?')}, key {ref[key].get('key', '?')}"
    if "fieldRef" in ref:
        return f"field {ref['fieldRef'].get('fieldPath', '?')}"
    if "resourceFieldRef" in ref:
        return f"resource {ref['resourceFieldRef'].get('resource', '?')}"
    return "another source"


def pod_spec_of(raw):
    return ((raw.get("spec") or {}).get("template") or {}).get("spec") or {}


def container_lists(raw):
    """[(field, index, container)] for the init containers and then the regular ones."""
    spec = pod_spec_of(raw)
    return [(field, i, c) for field in ("initContainers", "containers") for i, c in enumerate(spec.get(field) or [])
            if isinstance(c, dict)]


def workload_warnings(kind, raw):
    """Why changing this workload needs care."""
    out = []
    if (raw.get("metadata") or {}).get("namespace") == "kube-system":
        out.append("Workloads in kube-system usually belong to the cluster's own components (DNS, networking, the "
                   "proxy, add-ons); a wrong change can break the cluster.")
    return out


def volume_names(raw, values):
    """Names a container may mount: the pod's volumes as edited, plus a StatefulSet's volume claim templates."""
    names = {v.get("name", "") for v in values.get("pod", {}).get("volumes", [])}
    for claim in (raw.get("spec") or {}).get("volumeClaimTemplates") or []:
        names.add((claim.get("metadata") or {}).get("name", ""))
    return names


# ---- probes, volumes, security: rows <-> manifest ------------------------------------
def probe_values(probe):
    """The form's view of a probe dict (None = no probe)."""
    probe = probe or {}
    mode = next((m for m, key in PROBE_HANDLER.items() if key in probe), "None")
    handler = probe.get(PROBE_HANDLER.get(mode, ""), {}) or {}
    return {"mode": mode, "path": str(handler.get("path", "")), "scheme": str(handler.get("scheme", "")),
            "port": "" if handler.get("port") is None else str(handler["port"]),
            "command": [str(x) for x in handler.get("command") or []], "service": str(handler.get("service", "")),
            "timings": {key: "" if probe.get(key) is None else str(probe[key]) for key, _l in PROBE_TIMINGS}}


def build_probe(original, p):
    """The probe dict for the form's values (None for mode None). Fields the form does not show (HTTP headers, host, ...)
    are kept when the handler type did not change."""
    if p["mode"] == "None":
        return None
    probe = deepcopy(original or {})
    key = PROBE_HANDLER[p["mode"]]
    for other in PROBE_HANDLER.values():
        if other != key:
            probe.pop(other, None)
    handler = probe.get(key) or {}
    if p["mode"] == "Command":
        handler = {**{k: v for k, v in handler.items() if k != "command"}, "command": list(p["command"])}
    else:
        if p["port"].strip():
            handler["port"] = number_or_text(p["port"])
        if p["mode"] == "HTTP GET":
            for field in ("path", "scheme"):
                if p[field].strip():
                    handler[field] = p[field].strip()
                else:
                    handler.pop(field, None)
        if p["mode"] == "gRPC":
            if p["service"].strip():
                handler["service"] = p["service"].strip()
            else:
                handler.pop("service", None)
    probe[key] = handler
    for field, _label in PROBE_TIMINGS:
        text = p["timings"].get(field, "").strip()
        if text:
            probe[field] = int(text) if text.isdigit() else text
        else:
            probe.pop(field, None)
    return probe


def validate_probe(label, p):
    if p["mode"] == "None":
        return []
    out = []
    port = p["port"].strip()
    if p["mode"] != "Command" and not port:
        out.append(f"{label} probe: enter a port")
    elif p["mode"] == "gRPC" and port and not port.isdigit():
        out.append(f"{label} probe: a gRPC port must be a number")
    elif port and not port.isdigit() and not DNS_LABEL.fullmatch(port):
        out.append(f"{label} probe: the port must be a number or a port name")
    if p["mode"] == "HTTP GET" and p["path"].strip() and not p["path"].strip().startswith("/"):
        out.append(f"{label} probe: the path must start with /")
    if p["mode"] == "Command" and not [c for c in p["command"] if c.strip()]:
        out.append(f"{label} probe: enter the command to run")
    for field, name in PROBE_TIMINGS:
        text = p["timings"].get(field, "").strip()
        if text and not text.isdigit():
            out.append(f"{label} probe: {name.lower()} must be a whole number")
    return out


def volume_row(volume):
    """The table texts of a volume: name, type and the one detail the form edits."""
    kind = next((t for t in VOLUME_TYPES if t in volume), OTHER_VOLUME)
    source = volume.get(kind) if isinstance(volume.get(kind), dict) else {}
    detail = source.get("medium", "") if kind == "emptyDir" else source.get(VOLUME_KEYS.get(kind, ""), "")
    return {"name": volume.get("name", ""), "type": kind, "detail": str(detail or "")}


def build_volume(original, row):
    """A volume dict from table texts; an unknown type keeps its source, an unchanged type keeps its extra fields."""
    volume = deepcopy(original or {})
    volume["name"] = row["name"].strip()
    kind = row["type"]
    if kind == OTHER_VOLUME:
        return volume
    for other in VOLUME_TYPES:
        if other != kind:
            volume.pop(other, None)
    source = volume.get(kind) if isinstance(volume.get(kind), dict) else {}
    key = "medium" if kind == "emptyDir" else VOLUME_KEYS[kind]
    if row["detail"].strip():
        source[key] = row["detail"].strip()
    else:
        source.pop(key, None)
    volume[kind] = source
    return volume


def env_from_row(entry):
    ref = "secretRef" if "secretRef" in entry else "configMapRef"
    return {"source": "Secret" if ref == "secretRef" else "ConfigMap", "name": (entry.get(ref) or {}).get("name", ""),
            "prefix": entry.get("prefix", "")}


def build_env_from(original, row):
    entry = deepcopy(original or {})
    want = "secretRef" if row["source"] == "Secret" else "configMapRef"
    old = entry.pop("secretRef" if want == "configMapRef" else "configMapRef", None) or {}
    ref = entry.get(want) or old
    ref["name"] = row["name"].strip()
    entry[want] = ref
    if row["prefix"].strip():
        entry["prefix"] = row["prefix"].strip()
    else:
        entry.pop("prefix", None)
    return entry


def build_security(original, s):
    """A securityContext from the form's values (text ids, True/False/None flags); other keys are kept."""
    ctx = deepcopy(original or {})
    for key, value in s.items():
        if isinstance(value, str):
            text = value.strip()
            if text:
                ctx[key] = int(text) if text.isdigit() else text
            else:
                ctx.pop(key, None)
        elif value is None:
            ctx.pop(key, None)
        else:
            ctx[key] = bool(value)
    return ctx or None


def validate_security(label, s):
    return [f"{label}: {key} must be a whole number" for key, value in s.items()
            if isinstance(value, str) and value.strip() and not value.strip().isdigit()]


# ---- validation -----------------------------------------------------------------------
def validate(kind, values, raw=None):
    """Problems with the edited values (a list of sentences; empty = fine)."""
    problems = []
    if values.get("strategy_type") == "RollingUpdate":
        for key, label, number in STRATEGIES[kind][2]:
            text = values.get("knobs", {}).get(key, "").strip()
            percent = number == "count_or_percent"
            if text and not valid_count(text, percent=percent):
                problems.append(f"{label.split(' (')[0]}: enter a whole number" + (" or a percentage such as 25%" if percent else ""))
    for field, label in (("min_ready", "Min ready seconds"), ("history", "Revisions kept"),
                         ("progress_deadline", "Progress deadline")):
        text = values.get(field, "").strip()
        if text and not valid_count(text):
            problems.append(f"{label}: enter a whole number or leave it empty")
    problems += validate_pairs(values.get("annotations_pairs", []), "annotation")
    problems += validate_pairs(values.get("labels_pairs", []), "label")
    pod = values.get("pod")
    if pod is not None:
        problems += _validate_pod(raw or {}, pod)
    for c in values.get("containers", []):
        problems += _validate_container(c, volume_names(raw or {}, values) if raw is not None else None)
    return problems


def _validate_pod(raw, pod):
    out = []
    grace = pod["grace"].strip()
    if grace and not grace.isdigit():
        out.append("Termination grace period: enter a whole number of seconds or leave it empty")
    out += validate_pairs(pod["node_selector_pairs"], "node selector")
    out += validate_pairs(pod["template_labels_pairs"], "pod label")
    out += validate_pairs(pod["template_annotations_pairs"], "pod annotation")
    for n, t in enumerate(pod["tolerations"], 1):
        if t.get("operator") == "Exists" and t.get("value"):
            out.append(f"Toleration {n}: a value is not allowed with the operator Exists")
        if t.get("operator", "Equal") == "Equal" and not t.get("key"):
            out.append(f"Toleration {n}: enter a key (or use the operator Exists to tolerate everything)")
        if t.get("operator") not in (None, *TOLERATION_OPERATORS):
            out.append(f"Toleration {n}: the operator is Equal or Exists")
        if t.get("effect") not in (None, *TAINT_EFFECTS):
            out.append(f"Toleration {n}: the effect is {', '.join(TAINT_EFFECTS)} or empty")
        if "tolerationSeconds" in t and not isinstance(t["tolerationSeconds"], int):
            out.append(f"Toleration {n}: the seconds must be a whole number")
    seen = set()
    for v in pod["volumes"]:
        name = v.get("name", "")
        if not DNS_LABEL.fullmatch(name) or len(name) > 63:
            out.append(f"Volume \"{name}\": the name must be lowercase letters, digits and '-' (at most 63)")
        elif name in seen:
            out.append(f"Volume {name} is listed twice")
        seen.add(name)
        row = volume_row(v)
        if row["type"] in VOLUME_KEYS and not row["detail"].strip():
            out.append(f"Volume {name or '?'}: enter the {VOLUME_KEYS[row['type']]}")
    out += validate_security("Pod security", pod["security"])
    selector = ((raw.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
    labels = pod["template_labels"]
    missing = [f"{k}={v}" for k, v in selector.items() if labels.get(k) != v]
    if missing:
        out.append("The pod labels must keep the labels the selector looks for (" + ", ".join(missing) + "), "
                   "or the workload would lose its pods")
    return out


def _validate_container(c, volumes):
    out, name = [], c["name"]
    if not c["image"].strip():
        out.append(f"{name}: enter an image")
    numbers = {}
    for group in ("requests", "limits"):
        for res, label, _hint in RESOURCES:
            text = c[group].get(res, "").strip()
            if text and not valid_quantity(text):
                out.append(f"{name}: {label} {group[:-1]} \"{text}\" is not a valid quantity")
            elif text:
                numbers[group, res] = quantity_value(text)
    for res, label, _hint in RESOURCES:
        low, high = numbers.get(("requests", res)), numbers.get(("limits", res))
        if low is not None and high is not None and low > high:
            out.append(f"{name}: the {label} request is higher than the limit")
    seen = set()
    for entry in c["env"]:
        var = entry.get("name", "")
        if not ENV_NAME.fullmatch(var):
            out.append(f"{name}: \"{var}\" is not a valid variable name")
        elif var in seen:
            out.append(f"{name}: the variable {var} is listed twice")
        seen.add(var)
    for entry in c.get("env_from", []):
        if not env_from_row(entry)["name"].strip():
            out.append(f"{name}: an \"environment from\" entry needs a ConfigMap or Secret name")
    for p in c.get("ports", []):
        port = p.get("containerPort")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            out.append(f"{name}: a container port must be a number from 1 to 65535")
        if p.get("protocol") not in (None, *PROTOCOLS):
            out.append(f"{name}: the protocol is {', '.join(PROTOCOLS)}")
        if p.get("name") and (not DNS_LABEL.fullmatch(p["name"]) or len(p["name"]) > 15):
            out.append(f"{name}: port name \"{p.get('name')}\" must be lowercase letters, digits and '-' (at most 15)")
    for m in c.get("mounts", []):
        if not m.get("mountPath", "").startswith("/"):
            out.append(f"{name}: the mount path of {m.get('name') or '?'} must be an absolute path")
        if volumes is not None and m.get("name") not in volumes:
            out.append(f"{name}: it mounts {m.get('name') or '?'}, which is not one of the pod's volumes")
    for key, label in PROBE_NAMES:
        out += validate_probe(f"{name}: {label.lower()}", c.get("probes", {}).get(key, {"mode": "None"}))
    out += validate_security(name, {k: v for k, v in c.get("security", {}).items() if k in dict(CONTAINER_IDS)})
    return out


def image_hints(values):
    """Soft advice (not errors) about what was typed."""
    out = []
    for c in values.get("containers", []):
        image = c["image"].strip()
        last = image.rsplit("/", 1)[-1]
        if image and "@" not in image and (":" not in last or last.endswith(":latest")):
            out.append(f"{c['name']}: the image has no fixed tag, so a restart can pull a different version")
        if c.get("security", {}).get("privileged"):
            out.append(f"{c['name']}: a privileged container has almost full access to the node")
    return out


# ---- the manifest ---------------------------------------------------------------------------
def _set_or_pop(mapping, key, value):
    if value in ("", None, [], {}):
        mapping.pop(key, None)
    else:
        mapping[key] = value


def _int_or_none(text):
    return int(text) if str(text).strip() else None


def build_body(kind, raw, values):
    """The manifest with the edited fields; everything else is copied (see the module notes for `values`)."""
    body = deepcopy(raw)
    spec = body.setdefault("spec", {})
    if kind in HAS_REPLICAS and values.get("replicas") is not None:
        spec["replicas"] = int(values["replicas"])
    _set_or_pop(spec, "minReadySeconds", _int_or_none(values.get("min_ready", "")))
    _set_or_pop(spec, "revisionHistoryLimit", _int_or_none(values.get("history", "")))
    if kind == "Deployment":
        _set_or_pop(spec, "progressDeadlineSeconds", _int_or_none(values.get("progress_deadline", "")))
        if values.get("paused") is None:
            spec.pop("paused", None)
        else:
            spec["paused"] = bool(values["paused"])
    field, types, knobs = STRATEGIES[kind]
    strategy = spec.get(field) or {}
    strategy["type"] = values["strategy_type"]
    if values["strategy_type"] == "RollingUpdate":
        rolling = strategy.get("rollingUpdate") or {}
        for key, _label, _number in knobs:
            text = values.get("knobs", {}).get(key, "").strip()
            _set_or_pop(rolling, key, number_or_text(text) if text else None)
        _set_or_pop(strategy, "rollingUpdate", rolling or None)
    else:
        strategy.pop("rollingUpdate", None)
    if strategy != {"type": types[0]} or field in spec:  # not set before and still the default: leave it unset
        spec[field] = strategy
    template = spec.setdefault("template", {})
    pod = template.setdefault("spec", {})
    _build_pod(template, pod, values.get("pod"))
    for c in values.get("containers", []):
        _build_container(pod[c["field"]][c["index"]], c)
    meta = body.setdefault("metadata", {})
    for key in ("labels", "annotations"):
        _set_or_pop(meta, key, dict(values.get(key) or {}))
    return body


def _build_pod(template, pod, p):
    if p is None:
        return
    _set_or_pop(pod, "serviceAccountName", p["service_account"].strip())
    _set_or_pop(pod, "priorityClassName", p["priority_class"].strip())
    _set_or_pop(pod, "terminationGracePeriodSeconds", _int_or_none(p["grace"]))
    _set_or_pop(pod, "nodeSelector", dict(p["node_selector"]))
    _set_or_pop(pod, "tolerations", deepcopy(p["tolerations"]))
    _set_or_pop(pod, "imagePullSecrets", [{"name": n} if not isinstance(n, dict) else deepcopy(n) for n in p["pull_secrets"]])
    _set_or_pop(pod, "volumes", deepcopy(p["volumes"]))
    _set_or_pop(pod, "securityContext", build_security(pod.get("securityContext"), p["security"]))
    meta = template.setdefault("metadata", {})
    _set_or_pop(meta, "labels", dict(p["template_labels"]))
    _set_or_pop(meta, "annotations", dict(p["template_annotations"]))
    if not meta:
        template.pop("metadata", None)


def _build_container(target, c):
    target["image"] = c["image"].strip()
    _set_or_pop(target, "imagePullPolicy", c["pull"])
    resources = target.get("resources") or {}
    for group in ("requests", "limits"):
        current = resources.get(group) or {}
        for res, _label, _hint in RESOURCES:
            _set_or_pop(current, res, c[group].get(res, "").strip())
        _set_or_pop(resources, group, current or None)
    _set_or_pop(target, "resources", resources or None)
    _set_or_pop(target, "env", [deepcopy(e) for e in c["env"]])
    _set_or_pop(target, "envFrom", deepcopy(c["env_from"]))
    _set_or_pop(target, "command", [x for x in c["command"] if x != ""])
    _set_or_pop(target, "args", [x for x in c["args"] if x != ""])
    _set_or_pop(target, "ports", deepcopy(c["ports"]))
    _set_or_pop(target, "volumeMounts", deepcopy(c["mounts"]))
    for key, _label in PROBE_NAMES:
        _set_or_pop(target, key, build_probe(target.get(key), c["probes"][key]))
    _set_or_pop(target, "securityContext", build_security(target.get("securityContext"), c["security"]))


def template_changed(raw, body):
    return (raw.get("spec") or {}).get("template") != (body.get("spec") or {}).get("template")
