"""Readable HTML summary for Kubernetes Node details."""
from . import theme
from .rbac_view import _e, _section, _table
from .status_tone import status_tone


def _badge(text, tone=None):
    colors = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.BAD}
    symbols = {"ok": "✔", "warn": "▲", "bad": "✖"}
    color = colors.get(tone, theme.MUTED)
    return f"<b style='color:{color}'>{symbols.get(tone, '●')} {_e(text)}</b>"


def render(raw):
    raw = raw if isinstance(raw, dict) else {}
    meta, spec, status = raw.get("metadata") or {}, raw.get("spec") or {}, raw.get("status") or {}
    info = status.get("nodeInfo") or {}
    labels = meta.get("labels") or {}
    instance_id = next((labels.get(key) for key in (
        "node.kubernetes.io/instance-id", "failure-domain.beta.kubernetes.io/instance-id",
        "beta.kubernetes.io/instance-id") if labels.get(key)), "")
    ami_id = next((labels.get(key) for key in (
        "node.kubernetes.io/ami-id", "eks.amazonaws.com/nodegroup-image", "alpha.eksctl.io/ami-id") if labels.get(key)), "")
    conditions = status.get("conditions") or []
    ready = next((c.get("status") for c in conditions if c.get("type") == "Ready"), "Unknown")
    tone = "ok" if ready == "True" else "bad" if ready == "False" else "warn"
    name = meta.get("name", "")
    html = [f"<h2 style='margin:0'>Node <span style='color:{theme.ACCENT_HI}'>{_e(name)}</span></h2>",
            f"<p style='margin:6px 0 10px'>{_badge('Ready' if ready == 'True' else 'Not Ready' if ready == 'False' else 'Unknown', tone)}</p>"]
    html.append(_section("Identity", _table(("Field", "Value"), [
        ("Roles", ", ".join(sorted(k.split("/", 1)[1] for k in (meta.get("labels") or {}) if k.startswith("node-role.kubernetes.io/"))) or "<none>"),
        ("Kubelet", info.get("kubeletVersion", "")),
        ("Kernel", info.get("kernelVersion", "")),
        ("OS / architecture", f"{info.get('operatingSystem', '')} / {info.get('architecture', '')}"),
        ("Container runtime", info.get("containerRuntimeVersion", "")),
        ("Provider ID", spec.get("providerID", "")),
        ("Instance ID", instance_id),
        ("AMI ID", ami_id),
        ("Internal IP", next((a.get("address") for a in status.get("addresses") or [] if a.get("type") == "InternalIP"), "")),
        ("Scheduling", "Cordoned" if spec.get("unschedulable") else "Schedulable"),
    ])))
    alloc = status.get("allocatable") or {}
    capacity = status.get("capacity") or {}
    html.append(_section("Capacity", _table(("Resource", "Value"), [
        ("CPU", f"{alloc.get('cpu', '')} allocatable / {capacity.get('cpu', '')} capacity"),
        ("Memory", f"{alloc.get('memory', '')} allocatable / {capacity.get('memory', '')} capacity"),
        ("Pods", f"{alloc.get('pods', '')} allocatable / {capacity.get('pods', '')} capacity"),
    ])))
    if conditions:
        html.append(_section("Conditions", _table(("Condition", "Value"), [
            (c.get("type", ""), f"{c.get('status', '')} — {c.get('reason', '') or c.get('message', '')}")
            for c in conditions
        ])))
    taints = spec.get("taints") or []
    if taints:
        html.append(_section("Taints", _table(("Key", "Effect"), [
            (t.get("key", ""), f"{t.get('effect', '')}{' = ' + str(t.get('value')) if t.get('value') else ''}")
            for t in taints
        ])))
    annotations = meta.get("annotations") or {}
    if labels:
        html.append(_section("Labels", _table(("Key", "Value"), [(k, v) for k, v in sorted(labels.items())])))
    if annotations:
        html.append(_section("Annotations", _table(("Key", "Value"), [(k, v) for k, v in sorted(annotations.items())])))
    return "".join(html)
