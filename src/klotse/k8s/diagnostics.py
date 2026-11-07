"""A diagnostics bundle of one namespace: manifests, events, node conditions and pod logs in a zip file, for an incident or a support
ticket. Read-only: it only lists and reads. **Secrets are never read.** Everything else is cleaned before it is written:
`managedFields` and the last-applied-configuration annotation (which repeats the whole spec) are removed, ConfigMaps keep their key
names but not their values, and the values of environment variables are replaced by `<redacted>` unless the user asked to keep
them. What cannot be cleaned is said in the README of the bundle: pod logs are written as they are, and command lines and
arguments are not rewritten, so the user must look before sharing."""
import copy
import re
import threading
import zipfile
from datetime import datetime, timezone

import yaml

LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
REDACTED = "<redacted>"
MAX_PODS = 60  # pods whose logs are collected
MAX_CONTAINERS = 200
MAX_LOG_BYTES = 2_000_000  # per log; the end is kept
KINDS = [  # (zip name, how it is listed): "rows" uses KubeService.list_resource(key, namespace)
    ("pods", "pods"), ("deployments", "deployments"), ("statefulsets", "statefulsets"), ("daemonsets", "daemonsets"),
    ("jobs", "jobs"), ("cronjobs", "cronjobs"), ("services", "services"), ("ingresses", "ingresses"),
    ("networkpolicies", "networkpolicies"), ("persistentvolumeclaims", "persistentvolumeclaims"),
    ("horizontalpodautoscalers", "horizontalpodautoscalers"), ("poddisruptionbudgets", "poddisruptionbudgets"),
    ("resourcequotas", "resourcequotas"), ("configmaps", "configmaps")]


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(text))[:120] or "_"


def _redact_env(node):
    """Replace the literal value of every environment variable found anywhere in `node` (in place)."""
    if isinstance(node, dict):
        env = node.get("env")
        if isinstance(env, list):
            for var in env:
                if isinstance(var, dict) and "value" in var:
                    var["value"] = REDACTED
        for value in node.values():
            _redact_env(value)
    elif isinstance(node, list):
        for item in node:
            _redact_env(item)


def clean(raw, kind="", keep_env=False):
    """A copy of a manifest that is safe to put in a bundle (see the module text)."""
    body = copy.deepcopy(raw or {})
    meta = body.get("metadata")
    if isinstance(meta, dict):
        meta.pop("managedFields", None)
        annotations = meta.get("annotations")
        if isinstance(annotations, dict):
            annotations.pop(LAST_APPLIED, None)
            if not annotations:
                meta.pop("annotations")
    if kind == "configmaps":  # key names and sizes are enough to diagnose a mount; values may hold anything
        for field in ("data", "binaryData"):
            if isinstance(body.get(field), dict):
                body[field] = {k: f"<{len(str(v))} characters, not included>" for k, v in body[field].items()}
    if not keep_env:
        _redact_env(body)
    return body


def to_yaml(items):
    """Multi-document YAML text for a list of manifests."""
    return yaml.safe_dump_all(items, sort_keys=False, allow_unicode=True, default_flow_style=False) if items else ""


def events_text(events):
    """A readable table of events, oldest first: time, type, reason, object, count, message."""
    lines = ["TIME\tTYPE\tREASON\tOBJECT\tCOUNT\tMESSAGE"]
    for event in sorted(events, key=lambda e: str(e.get("lastTimestamp") or e.get("eventTime") or (e.get("metadata") or {}).get("creationTimestamp") or "")):
        obj = event.get("involvedObject") or {}
        when = event.get("lastTimestamp") or event.get("eventTime") or (event.get("metadata") or {}).get("creationTimestamp") or ""
        message = str(event.get("message", "")).replace("\n", " ")
        lines.append(f"{when}\t{event.get('type', '')}\t{event.get('reason', '')}\t{obj.get('kind', '')}/{obj.get('name', '')}\t{event.get('count', 1)}\t{message}")
    return "\n".join(lines) + "\n"


def nodes_text(nodes):
    """One block per node: name, conditions (type=status reason), capacity and allocatable."""
    blocks = []
    for node in nodes:
        status = node.get("status") or {}
        lines = [(node.get("metadata") or {}).get("name", "?")]
        for c in status.get("conditions") or []:
            lines.append(f"  {c.get('type')}={c.get('status')} {c.get('reason', '')} {str(c.get('message', ''))[:160]}".rstrip())
        lines.append(f"  capacity: {status.get('capacity') or {}}")
        lines.append(f"  allocatable: {status.get('allocatable') or {}}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def readme(context, namespace, version, options, notes, counts):
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"Klotse diagnostics bundle", f"Cluster context: {context}", f"Namespace: {namespace}", f"Made: {when} by Klotse {version}", "",
             "What is inside", "  objects/<kind>.yaml   manifests (managedFields and last-applied annotations removed)",
             "  events.txt            events of the namespace, oldest first", "  nodes.txt             conditions and capacity of the nodes that run the pods",
             "  logs/<pod>_<container>[.previous].log   the last lines of each container" + ("" if options["logs"] else " (NOT collected this time)"), "",
             "What was left out or changed", "  Secrets: never read.", "  ConfigMaps: key names only, values are not included.",
             "  Environment variable values: " + ("kept as they are (the user asked)." if options["keep_env"] else f"replaced by {REDACTED}."), "",
             "What was NOT cleaned (look before you share this file)",
             "  Pod logs are written exactly as the containers wrote them; they can contain tokens, addresses or personal data.",
             "  Command lines and arguments in manifests are not rewritten, and neither are annotations and labels.", "",
             "Counts: " + ", ".join(f"{k} {v}" for k, v in counts.items())]
    if notes:
        lines += ["", "Things that could not be collected"] + [f"  {n}" for n in notes]
    return "\n".join(lines) + "\n"


class Cancelled(Exception):
    pass


def collect(service, context, namespace, path, options, version="", progress=lambda text: None, cancel=None):
    """Write the bundle for `namespace` to the zip file `path`. `options`: logs (bool), tail (lines per container), keep_env (bool).
    Returns {"counts": {...}, "notes": [what could not be read]}. Raises Cancelled when `cancel` (a threading.Event) is set, after
    removing nothing: the caller deletes the unfinished file. A kind the user may not list is skipped and noted, never fatal."""
    cancel = cancel or threading.Event()
    notes, counts = [], {}

    def check():
        if cancel.is_set():
            raise Cancelled()

    def listed(label, read):
        check()
        progress(f"Reading {label}…")
        try:
            return [row.raw for row in read()]
        except Exception as error:  # noqa: BLE001 - one forbidden kind must not stop the bundle
            notes.append(f"{label}: {type(error).__name__}: {str(error).splitlines()[0][:150] if str(error) else ''}")
            return []

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        pods = []
        for name, key in KINDS:
            if key == "pods":
                read_pods = getattr(service, "list_pods_for_analysis", None)  # big clusters: whole manifests from the plain JSON
                items = pods = listed("pods", (lambda: read_pods(namespace, full=True)) if read_pods else (lambda: service.list_pods(namespace)))
            elif key == "deployments":
                items = listed("deployments", lambda: service.list_deployments(namespace))
            elif key == "statefulsets":
                items = listed("statefulsets", lambda: service.list_statefulsets(namespace))
            else:
                items = listed(name, lambda k=key: service.list_resource(k, namespace))
            counts[name] = len(items)
            if items:
                bundle.writestr(f"objects/{name}.yaml", to_yaml([clean(i, name, options["keep_env"]) for i in items]))
        events = listed("events", lambda: service.list_resource("events", namespace))
        counts["events"] = len(events)
        bundle.writestr("events.txt", events_text(events))
        wanted_nodes = {(p.get("spec") or {}).get("nodeName") for p in pods} - {None, ""}
        nodes = [n for n in listed("nodes", service.list_nodes) if (n.get("metadata") or {}).get("name") in wanted_nodes]
        counts["nodes"] = len(nodes)
        bundle.writestr("nodes.txt", nodes_text(nodes))
        if options["logs"]:
            logged = 0
            for pod in pods[:MAX_PODS]:
                meta = pod.get("metadata") or {}
                statuses = {cs.get("name"): cs for cs in (pod.get("status") or {}).get("containerStatuses") or []}
                for container in (pod.get("spec") or {}).get("containers") or []:
                    if logged >= MAX_CONTAINERS:
                        break
                    check()
                    cname = container.get("name", "")
                    progress(f"Reading logs of {meta.get('name')}/{cname}…")
                    base = f"logs/{safe_name(meta.get('name'))}_{safe_name(cname)}"
                    for previous in (False, True) if (statuses.get(cname) or {}).get("restartCount") else (False,):
                        try:
                            text = service.read_pod_log(namespace, meta.get("name"), cname, options["tail"], previous)
                        except Exception as error:  # noqa: BLE001
                            notes.append(f"log of {meta.get('name')}/{cname}{' (previous)' if previous else ''}: {type(error).__name__}")
                            continue
                        data = text.encode("utf-8", "replace")
                        if len(data) > MAX_LOG_BYTES:
                            data = b"[earlier lines cut: the log is larger than the limit]\n" + data[-MAX_LOG_BYTES:]
                        bundle.writestr(base + (".previous" if previous else "") + ".log", data)
                        logged += 1
            counts["logs"] = logged
            if len(pods) > MAX_PODS:
                notes.append(f"logs: only the first {MAX_PODS} of {len(pods)} pods were read")
        bundle.writestr("README.txt", readme(context, namespace, version, options, notes, counts))
    return {"counts": counts, "notes": notes}
