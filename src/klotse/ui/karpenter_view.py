"""Read-only summaries for Karpenter resources, built only from their loaded manifests.

Fields follow the v1/v1beta1 resources documented at karpenter.sh/docs/concepts/
nodepools, nodeclaims and nodeclasses. Optional/version-specific fields remain optional.
"""
import json

from . import theme
from .rbac_view import _e, _section, _when
from .related_view import link
from ..k8s.resources import selector_terms_text

GROUPS = {"NodePool": "karpenter.sh", "NodeClaim": "karpenter.sh", "EC2NodeClass": "karpenter.k8s.aws"}
MAX_ITEMS = 40


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def kind_of(raw, kind=None, group=None):
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind") or kind
    api = raw.get("apiVersion")
    group = str(api).split("/", 1)[0] if api else group
    return kind if kind in GROUPS and group == GROUPS[kind] else None


def _text(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, bool):
        value = "Yes" if value else "No"
    value = str(value)
    return _e(value[:1000] + ("… (full value in YAML)" if len(value) > 1000 else ""))


def _facts(rows):
    rows = [(k, v) for k, v in rows if v is not None and v != "" and v != {} and v != []]
    if not rows:
        return f"<p style='color:{theme.MUTED}'>Not reported.</p>"
    return "<table cellspacing='0' width='100%'>" + "".join(
        f"<tr><td valign='top' style='color:{theme.MUTED};padding:3px 12px 3px 0'>{_e(k)}</td>"
        f"<td valign='top' style='padding:3px 0'>{_text(v)}</td></tr>" for k, v in rows) + "</table>"


def _object(kind, name):
    if not name:
        return ""
    return f"<a href='{_e(link(kind, '', str(name)))}'>{_e(kind)} {_e(name)}</a>"


def _list_section(title, values, formatter=_text):
    values = _items(values)
    if not values:
        return ""
    result = "<br>".join(formatter(v) for v in values[:MAX_ITEMS])
    if len(values) > MAX_ITEMS:
        result += f"<p style='color:{theme.MUTED}'>Showing {MAX_ITEMS} of {len(values)}; see YAML for the complete list.</p>"
    return _section(title, result)


def _requirements(values):
    def requirement(value):
        value = _mapping(value)
        parts = [value.get("key", "?"), value.get("operator", "?"), ", ".join(map(str, _items(value.get("values"))))]
        if "minValues" in value:
            parts.append(f"minimum distinct values: {value['minValues']}")
        return _e(" · ".join(str(p) for p in parts if p))
    return _list_section("Requirements", values, requirement)


def _conditions(status):
    def condition(value):
        value = _mapping(value)
        return _facts([(value.get("type", "Condition"), value.get("status", "Unknown")),
                       ("Reason", value.get("reason")), ("Message", value.get("message")),
                       ("Last transition", _when(value["lastTransitionTime"]) if value.get("lastTransitionTime") else None)])
    return _list_section("Conditions", status.get("conditions"), condition)


def _class_ref(spec):
    ref = _mapping(spec.get("nodeClassRef"))
    name, kind = ref.get("name"), ref.get("kind") or "EC2NodeClass"
    return _section("Node class", _object(kind, name)) if name else ""


def _nodepool(raw):
    spec, status = _mapping(raw.get("spec")), _mapping(raw.get("status"))
    template = _mapping(spec.get("template"))
    pod, meta = _mapping(template.get("spec")), _mapping(template.get("metadata"))
    disruption = _mapping(spec.get("disruption"))
    result = _class_ref(pod)
    result += _section("Allocation and limits", _facts([
        ("Nodes", status.get("nodes", _mapping(status.get("resources")).get("nodes"))),
        ("Allocated resources", status.get("resources")), ("Resource limits", spec.get("limits")),
        ("Replicas", spec.get("replicas")), ("Weight", spec.get("weight"))]))
    result += _requirements(pod.get("requirements"))
    result += _section("Node template", _facts([("Labels", meta.get("labels")), ("Taints", pod.get("taints")),
        ("Startup taints", pod.get("startupTaints")), ("Expire after", pod.get("expireAfter")),
        ("Termination grace period", pod.get("terminationGracePeriod"))]))
    result += _section("Disruption", _facts([("Consolidation policy", disruption.get("consolidationPolicy")),
        ("Consolidate after", disruption.get("consolidateAfter")), ("Budgets", disruption.get("budgets"))]))
    return result


def _nodeclaim(raw):
    spec, status, meta = _mapping(raw.get("spec")), _mapping(raw.get("status")), _mapping(raw.get("metadata"))
    labels = _mapping(meta.get("labels"))
    owner = next((o.get("name") for o in _items(meta.get("ownerReferences"))
                  if isinstance(o, dict) and o.get("kind") == "NodePool"), None) or labels.get("karpenter.sh/nodepool")
    links = [_object("NodePool", owner), _object("Node", status.get("nodeName"))]
    result = _section("Related objects", "<br>".join(v for v in links if v)) if any(links) else ""
    result += _class_ref(spec)
    result += _section("Instance and lifecycle", _facts([
        ("Provider ID", status.get("providerID")), ("Image ID", status.get("imageID")),
        ("Instance type", labels.get("node.kubernetes.io/instance-type")),
        ("Capacity type", labels.get("karpenter.sh/capacity-type")), ("Zone", labels.get("topology.kubernetes.io/zone")),
        ("Expire after", spec.get("expireAfter")), ("Termination grace period", spec.get("terminationGracePeriod"))]))
    result += _section("Resources", _facts([("Requested", _mapping(spec.get("resources")).get("requests")),
        ("Capacity", status.get("capacity")), ("Allocatable", status.get("allocatable"))]))
    result += _requirements(spec.get("requirements"))
    result += _section("Scheduling", _facts([("Taints", spec.get("taints")), ("Startup taints", spec.get("startupTaints"))]))
    return result


def _ec2class(raw):
    spec, status = _mapping(raw.get("spec")), _mapping(raw.get("status"))
    result = _section("Images", _facts([("AMI family", spec.get("amiFamily")),
        ("AMI selector", selector_terms_text(spec.get("amiSelectorTerms")))]))
    result += _list_section("Resolved AMIs", status.get("amis"))
    result += _section("Network selectors", _facts([
        ("Subnets", selector_terms_text(spec.get("subnetSelectorTerms"))),
        ("Security groups", selector_terms_text(spec.get("securityGroupSelectorTerms")))]))
    result += _list_section("Resolved subnets", status.get("subnets"))
    result += _list_section("Resolved security groups", status.get("securityGroups"))
    result += _section("Identity", _facts([("Role", spec.get("role")), ("Instance profile", spec.get("instanceProfile")),
        ("Resolved instance profile", status.get("instanceProfile"))]))
    result += _list_section("Block devices", spec.get("blockDeviceMappings"))
    result += _section("Instance configuration", _facts([
        ("Metadata options", spec.get("metadataOptions")), ("Instance store policy", spec.get("instanceStorePolicy")),
        ("Detailed monitoring", spec.get("detailedMonitoring")), ("Tags", spec.get("tags")),
        ("User data", "Configured; inspect YAML for the full value." if spec.get("userData") else None)]))
    return result


def render(kind, raw, include_header=True):
    """Render a supported kind; no network calls or evaluation of resource content."""
    if kind not in GROUPS or not isinstance(raw, dict):
        raise ValueError(f"No Karpenter summary for {kind}")
    meta, status = _mapping(raw.get("metadata")), _mapping(raw.get("status"))
    result = f"<h2>{_e(kind)} {_e(meta.get('name', ''))}</h2>" if include_header else ""
    result += _section("Lifecycle", _facts([
        ("Created", _when(meta["creationTimestamp"]) if meta.get("creationTimestamp") else None),
        ("Deletion requested", _when(meta["deletionTimestamp"]) if meta.get("deletionTimestamp") else None),
        ("Generation", meta.get("generation")), ("Observed generation", status.get("observedGeneration"))]))
    result += {"NodePool": _nodepool, "NodeClaim": _nodeclaim, "EC2NodeClass": _ec2class}[kind](raw)
    result += _conditions(status)
    return f"<div style='font-size:12px'>{result}</div>"
