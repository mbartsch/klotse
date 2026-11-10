"""Read-only network summaries from the already-loaded, untrusted manifest."""
from .rbac_view import _e, _section, _table
from .workload_view import _facts
from .related_view import link

KINDS = ("Service", "Ingress", "Endpoints", "EndpointSlice", "NetworkPolicy")
MAX_ROWS = 80


def _dict(value):
    return value if isinstance(value, dict) else {}


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _joined(value):
    return ", ".join(str(item) for item in value) if isinstance(value, list) else str(value or "")


def kind_of(raw, hint=None):
    kind = _dict(raw).get("kind") or hint
    return kind if kind in KINDS else None


def _rows(title, headings, rows):
    note = f"<p>Showing the first {MAX_ROWS} entries; YAML contains the full list.</p>" if len(rows) > MAX_ROWS else ""
    return _section(title, _table(headings, rows[:MAX_ROWS]) + note if rows else "No entries.")


def _reference(kind, namespace, name):
    if not name:
        return "—"
    return f"<a href='{_e(link(kind, namespace, str(name)))}'>{_e(name)}</a>"


def selector(value):
    value = _dict(value)
    parts = [f"{k}={v}" for k, v in _dict(value.get("matchLabels")).items()]
    for item in _objects(value.get("matchExpressions")):
        parts.append(f"{item.get('key', '')} {item.get('operator', '')} ({_joined(item.get('values'))})")
    return ", ".join(parts) or "all"


def _port(port):
    value = port.get("port", "all ports")
    if port.get("endPort") is not None:
        value = f"{value}–{port['endPort']}"
    return f"{value}/{port.get('protocol') or 'TCP'}"


def _load_balancers(raw):
    return ", ".join(str(item.get("hostname") or item.get("ip") or "")
                     for item in _objects(_dict(_dict(raw.get("status")).get("loadBalancer")).get("ingress")))


def _service(raw, spec, namespace):
    facts = [("Type", _e(spec.get("type") or "ClusterIP")),
             ("Cluster IPs", _e(_joined(spec.get("clusterIPs")) or spec.get("clusterIP") or "—")),
             ("External IPs", _e(_joined(spec.get("externalIPs")) or "—")),
             ("External name", _e(spec.get("externalName") or "—")),
             ("Load balancer", _e(_load_balancers(raw) or "—")),
             ("Selector", _e(", ".join(f"{k}={v}" for k, v in _dict(spec.get("selector")).items()) or "None (manually managed endpoints)")),
             ("Traffic policy", _e(spec.get("externalTrafficPolicy") or "Cluster"))]
    ports = [(_e(p.get("name") or "—"), _e(_port(p)), _e(p.get("targetPort", p.get("port", "—"))),
              _e(p.get("nodePort") or "—")) for p in _objects(spec.get("ports"))]
    return _section("Service", _facts(facts)) + _rows("Ports", ("Name", "Port", "Target", "Node port"), ports)


def _backend(value, namespace):
    value = _dict(value)
    service = _dict(value.get("service"))
    if service or value.get("serviceName"):
        port = _dict(service.get("port"))
        return _reference("Service", namespace, service.get("name") or value.get("serviceName")) + " : " + _e(
            port.get("name") or port.get("number") or value.get("servicePort") or "—")
    resource = _dict(value.get("resource"))
    return _e(f"{resource.get('kind', '')} {resource.get('name', '')}".strip() or "—")


def _ingress(raw, spec, namespace):
    annotations = _dict(_dict(raw.get("metadata")).get("annotations"))
    body = _section("Ingress", _facts([
        ("Class", _e(spec.get("ingressClassName") or annotations.get("kubernetes.io/ingress.class") or "—")),
        ("Load balancer", _e(_load_balancers(raw) or "—")),
        ("Default backend", _backend(spec.get("defaultBackend") or spec.get("backend"), namespace))]))
    routes = []
    for rule in _objects(spec.get("rules")):
        for path in _objects(_dict(rule.get("http")).get("paths")):
            routes.append((_e(rule.get("host") or "All hosts"), _e(path.get("path") or "/"),
                           _e(path.get("pathType") or "—"), _backend(path.get("backend"), namespace)))
    tls = [(_e(_joined(t.get("hosts")) or "—"), _reference("Secret", namespace, t.get("secretName")))
           for t in _objects(spec.get("tls"))]
    return body + _rows("Routes", ("Host", "Path", "Match", "Backend"), routes) + _rows("TLS", ("Hosts", "Secret reference"), tls)


def _endpoints(raw, namespace, sliced=False):
    addresses, ports = [], []
    subsets = [raw] if sliced else _objects(raw.get("subsets"))
    for subset in subsets:
        ports += [(_e(p.get("name") or "—"), _e(_port(p)), _e(p.get("appProtocol") or "—"))
                  for p in _objects(subset.get("ports"))]
        entries = [(e, None) for e in _objects(subset.get("endpoints"))] if sliced else (
            [(e, True) for e in _objects(subset.get("addresses"))] + [(e, False) for e in _objects(subset.get("notReadyAddresses"))])
        for entry, ready in entries:
            conditions = _dict(entry.get("conditions"))
            if sliced:
                ready = conditions.get("ready")
            target = _dict(entry.get("targetRef"))
            address = _joined(entry.get("addresses")) if sliced else entry.get("ip")
            ref = _reference(target.get("kind") or "Pod", target.get("namespace") or namespace, target.get("name"))
            state = "Ready" if ready is True else "Not ready" if ready is False else "Unknown"
            if conditions.get("terminating"):
                state += " · Terminating"
            addresses.append((_e(address or "—"), _e(state), ref, _e(entry.get("nodeName") or "—")))
    return (_section("Address family", _e(raw.get("addressType") or "Not specified")) if sliced else "") + _rows(
        "Addresses", ("Address", "Readiness", "Target", "Node"), addresses) + _rows("Ports", ("Name", "Port", "App protocol"), ports)


def _peer(peer):
    block = _dict(peer.get("ipBlock"))
    if block:
        return str(block.get("cidr") or "—") + (" except " + _joined(block["except"]) if block.get("except") else "")
    terms = []
    if "namespaceSelector" in peer:
        terms.append("namespaces: " + selector(peer["namespaceSelector"]))
    if "podSelector" in peer:
        terms.append("pods: " + selector(peer["podSelector"]))
        if "namespaceSelector" not in peer:
            terms.append("in this namespace")
    return " AND ".join(terms) or "All peers"


def _policy(spec):
    types = spec.get("policyTypes") or (["Ingress", "Egress"] if "egress" in spec else ["Ingress"])
    body = _section("Selection", _facts([("Pods in this namespace", _e(selector(spec.get("podSelector")))),
                                         ("Policy types", _e(_joined(types)))]))
    for direction, peer_key in (("Ingress", "from"), ("Egress", "to")):
        if direction not in types:
            body += _section(direction, "Not restricted by this policy.")
            continue
        rows = []
        for rule in _objects(spec.get(direction.lower())):
            peers = _objects(rule.get(peer_key))
            ports = _objects(rule.get("ports"))
            rows.append((_e(" OR ".join(_peer(p) for p in peers) or "All peers"),
                         _e(", ".join(_port(p) for p in ports) or "All ports")))
        body += _rows(direction, ("Allowed peers", "Allowed ports"), rows) if rows else _section(
            direction, "No traffic allowed by this policy in this direction. Other policies may add permissions.")
    return body


def render(kind, raw, include_header=True):
    if kind not in KINDS or not isinstance(raw, dict):
        raise ValueError(f"No network summary for {kind}")
    spec, meta = _dict(raw.get("spec")), _dict(raw.get("metadata"))
    namespace = meta.get("namespace") or ""
    if kind == "Service":
        body = _service(raw, spec, namespace)
    elif kind == "Ingress":
        body = _ingress(raw, spec, namespace)
    elif kind in ("Endpoints", "EndpointSlice"):
        body = _endpoints(raw, namespace, kind == "EndpointSlice")
    else:
        body = _policy(spec)
    header = f"<h2>{_e(kind)} {_e(meta.get('name', ''))}</h2>" if include_header else ""
    return f"<div style='font-size:12px'>{header}{body}</div>"
