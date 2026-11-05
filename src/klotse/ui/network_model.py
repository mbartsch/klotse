"""The data side of the Service, Ingress and NetworkPolicy editors (no Qt): skeletons, validation and building the manifest.

Each `build_*` returns a copy of the manifest with only the fields the form owns replaced; everything else (and everything
inside an entry that the form does not show) is kept. `validate_*` return a list of sentences (empty = fine)."""
import ipaddress
import re
from copy import deepcopy

from .service_account_editor import validate_pairs
from .workload_model import DNS_LABEL

NETWORK_KINDS = ("Service", "Ingress", "NetworkPolicy")
SERVICE_TYPES = ("ClusterIP", "NodePort", "LoadBalancer", "ExternalName")
PROTOCOLS = ("TCP", "UDP", "SCTP")
AFFINITY = ("None", "ClientIP")
TRAFFIC_POLICIES = ("", "Cluster", "Local")
PATH_TYPES = ("Prefix", "Exact", "ImplementationSpecific")
POLICY_TYPES = ("Ingress", "Egress")
PORT_NAME = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")  # an IANA service name: at most 15 characters, a letter in it
HOST = re.compile(r"(\*\.)?[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
SUBDOMAIN = re.compile(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?")
SELECTOR_ALL = "all"  # in a text box: an empty selector, which matches everything


def _set_or_pop(mapping, key, value):
    if value in ("", None, [], {}):
        mapping.pop(key, None)
    else:
        mapping[key] = value


def valid_port(value):
    return isinstance(value, int) and 1 <= value <= 65535


def valid_port_or_name(value):
    """A port number, or a port name (what a container calls its port)."""
    if isinstance(value, int):
        return valid_port(value)
    text = str(value)
    return len(text) <= 15 and PORT_NAME.fullmatch(text) is not None and re.search("[a-z]", text) is not None


def port_value(text):
    """'8080' -> 8080 and 'http' -> 'http': how Kubernetes stores a port that may be a name."""
    text = str(text).strip()
    return int(text) if text.isdigit() else text


def valid_cidr(text):
    try:
        ipaddress.ip_network(text.strip(), strict=False)
        return "/" in text
    except ValueError:
        return False


def valid_ip(text):
    try:
        ipaddress.ip_address(text.strip())
        return True
    except ValueError:
        return False


# ---- selectors as text ("app=web,tier=front", "all" = every one, empty = not set) --------------
def selector_text(selector):
    """The text a selector is shown as, or None when it has expressions (those are kept as they are, not edited here)."""
    if selector is None:
        return ""
    if selector.get("matchExpressions"):
        return None
    labels = selector.get("matchLabels") or {}
    return ",".join(f"{k}={v}" for k, v in labels.items()) if labels else SELECTOR_ALL


def parse_selector(text):
    """({matchLabels} or {} or None for not set, problem or ''). 'all' is the empty selector, which matches everything."""
    text = text.strip()
    if not text:
        return None, ""
    if text == SELECTOR_ALL:
        return {}, ""
    labels = {}
    for part in text.split(","):
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or not key or any(c.isspace() for c in key):
            return None, f"\"{part.strip()}\" is not key=value"
        labels[key] = value.strip()
    return {"matchLabels": labels}, ""


# ---- Service ----------------------------------------------------------------------------------------
def service_skeleton(namespace):
    return {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "", "namespace": namespace},
            "spec": {"type": "ClusterIP", "ports": [{"port": 80, "targetPort": 80, "protocol": "TCP"}]}}


def validate_service(values, create=False):
    out = []
    kind = values["type"]
    if kind == "ExternalName":
        name = values["external_name"].strip()
        if not name or not HOST.fullmatch(name.lower()):
            out.append("External name: enter the DNS name this service points to, for example db.example.com")
    names = [p.get("name", "") for p in values["ports"]]
    seen = set()
    for n, p in enumerate(values["ports"], 1):
        if not valid_port(p.get("port")):
            out.append(f"Port {n}: the port must be a number from 1 to 65535")
        if "targetPort" in p and not valid_port_or_name(p["targetPort"]):
            out.append(f"Port {n}: the target port is a number from 1 to 65535 or a port name")
        if p.get("protocol") not in (None, *PROTOCOLS):
            out.append(f"Port {n}: the protocol is {', '.join(PROTOCOLS)}")
        if "nodePort" in p and not valid_port(p["nodePort"]):
            out.append(f"Port {n}: the node port must be a number from 1 to 65535 (usually 30000-32767)")
        if p.get("name") and not DNS_LABEL.fullmatch(p["name"]):
            out.append(f"Port {n}: the name must be lowercase letters, digits and '-'")
        if len(values["ports"]) > 1 and not p.get("name"):
            out.append(f"Port {n}: a service with several ports needs a name for each")
        key = (p.get("port"), p.get("protocol", "TCP"))
        if key in seen:
            out.append(f"Port {n}: {key[0]}/{key[1]} is listed twice")
        seen.add(key)
    if len({n for n in names if n}) != len([n for n in names if n]):
        out.append("Two ports have the same name")
    if kind != "ExternalName" and not values["ports"] and values["cluster_ip"].strip() != "None":
        out.append("Add at least one port (only a headless service can have none)")
    if values["affinity_timeout"].strip() and (not values["affinity_timeout"].strip().isdigit()
                                                 or not 1 <= int(values["affinity_timeout"]) <= 86400):
        out.append("Session timeout: enter seconds from 1 to 86400")
    ip = values["cluster_ip"].strip()
    if create and ip and ip != "None" and not valid_ip(ip):
        out.append("Cluster IP: leave it empty, type None for a headless service, or give an address inside the cluster's range")
    for r in values["source_ranges"]:
        if not valid_cidr(r):
            out.append(f"\"{r}\" is not a CIDR range such as 10.0.0.0/8")
    out += validate_pairs(values["selector_pairs"], "selector label")
    return out


def service_hints(values):
    out = []
    if values["type"] != "ExternalName" and not values["selector"]:
        out.append("No selector: no pods receive traffic unless you create the endpoints yourself")
    if values["type"] == "LoadBalancer":
        out.append("A LoadBalancer asks the cloud provider for a load balancer, which usually costs money")
    return out


def build_service(raw, values, new=None):
    body = deepcopy(raw)
    if new:
        meta = body.setdefault("metadata", {})
        meta["name"], meta["namespace"] = new["name"].strip(), new["namespace"].strip()
    spec = body.setdefault("spec", {})
    kind = values["type"]
    if kind != "ClusterIP" or "type" in spec:
        spec["type"] = kind
    _set_or_pop(spec, "selector", dict(values["selector"]) if kind != "ExternalName" else {})
    ports = deepcopy(values["ports"])
    if kind not in ("NodePort", "LoadBalancer"):
        for p in ports:
            p.pop("nodePort", None)
    _set_or_pop(spec, "ports", ports)
    if kind == "ExternalName":
        spec["externalName"] = values["external_name"].strip()
    else:
        spec.pop("externalName", None)
    if new and values["cluster_ip"].strip():
        spec["clusterIP"] = values["cluster_ip"].strip()  # cannot be changed after creation
    if values["affinity"] == "ClientIP" or "sessionAffinity" in spec:
        spec["sessionAffinity"] = values["affinity"]
    config = spec.get("sessionAffinityConfig") or {}
    if values["affinity"] == "ClientIP" and values["affinity_timeout"].strip():
        config.setdefault("clientIP", {})["timeoutSeconds"] = int(values["affinity_timeout"])
    elif values["affinity"] != "ClientIP":
        config = {}
    _set_or_pop(spec, "sessionAffinityConfig", config)
    if kind in ("NodePort", "LoadBalancer"):
        _set_or_pop(spec, "externalTrafficPolicy", values["ext_policy"])
    else:
        spec.pop("externalTrafficPolicy", None)
    _set_or_pop(spec, "internalTrafficPolicy", values["int_policy"])
    _set_or_pop(spec, "loadBalancerSourceRanges", list(values["source_ranges"]) if kind == "LoadBalancer" else [])
    if values["publish_not_ready"] is None:
        spec.pop("publishNotReadyAddresses", None)
    else:
        spec["publishNotReadyAddresses"] = bool(values["publish_not_ready"])
    _build_meta(body, values)
    return body


def _build_meta(body, values):
    meta = body.setdefault("metadata", {})
    for key in ("labels", "annotations"):
        _set_or_pop(meta, key, dict(values.get(key) or {}))


# ---- Ingress ------------------------------------------------------------------------------------------
def ingress_skeleton(namespace):
    return {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": "", "namespace": namespace},
            "spec": {"rules": [{"http": {"paths": [{"path": "/", "pathType": "Prefix",
                                                    "backend": {"service": {"name": "", "port": {"number": 80}}}}]}}]}}


def ingress_rows(spec):
    """One row per path: {"host", "path"} (the path dict as it is). Rules without paths are kept by build_ingress."""
    rows = []
    for rule in spec.get("rules") or []:
        for path in (rule.get("http") or {}).get("paths") or []:
            rows.append({"host": rule.get("host", ""), "path": deepcopy(path)})
    return rows


def backend_texts(backend):
    """(service name, port) texts of a path backend; a resource backend is shown as (resource ...) and kept."""
    backend = backend or {}
    if "resource" in backend:
        res = backend["resource"]
        return f"(resource {res.get('kind', '?')}/{res.get('name', '?')})", ""
    service = backend.get("service") or {}
    port = service.get("port") or {}
    return service.get("name", ""), str(port.get("number", port.get("name", "")))


def build_backend(original, name, port):
    """A backend from its texts: a resource backend that was not touched is returned as it was."""
    if name.startswith("(resource"):
        return deepcopy(original or {})
    backend = deepcopy(original or {})
    backend.pop("resource", None)
    service = backend.get("service") or {}
    service["name"] = name.strip()
    value = port_value(port) if str(port).strip() else None
    if value is None:
        service.pop("port", None)
    else:
        service["port"] = {"number": value} if isinstance(value, int) else {"name": value}
    backend["service"] = service
    return backend


def build_ingress(raw, values, new=None):
    body = deepcopy(raw)
    if new:
        meta = body.setdefault("metadata", {})
        meta["name"], meta["namespace"] = new["name"].strip(), new["namespace"].strip()
    spec = body.setdefault("spec", {})
    _set_or_pop(spec, "ingressClassName", values["class"].strip())
    originals = {r.get("host", ""): r for r in (raw.get("spec") or {}).get("rules") or []}
    grouped = {}
    for row in values["rows"]:
        grouped.setdefault(row["host"], []).append(row["path"])
    rules = []
    for host, paths in grouped.items():
        rule = deepcopy(originals.get(host, {}))
        if host:
            rule["host"] = host
        else:
            rule.pop("host", None)
        rule["http"] = {**(rule.get("http") or {}), "paths": paths}
        rules.append(rule)
    for host, rule in originals.items():  # host-only rules (no paths) are not in the table: keep them
        if host not in grouped and not (rule.get("http") or {}).get("paths"):
            rules.append(deepcopy(rule))
    _set_or_pop(spec, "rules", rules)
    default = values["default_backend"]
    if default is not None:
        if default["service"].strip():
            spec["defaultBackend"] = build_backend(spec.get("defaultBackend"), default["service"], default["port"])
        elif "resource" not in (spec.get("defaultBackend") or {}):
            spec.pop("defaultBackend", None)
    _set_or_pop(spec, "tls", deepcopy(values["tls"]))
    _build_meta(body, values)
    return body


def validate_ingress(values):
    out = []
    if not values["rows"] and not values["default_backend"]["service"].strip():
        out.append("Add a rule or a default backend, or the ingress sends nothing anywhere")
    seen = set()
    for n, row in enumerate(values["rows"], 1):
        host, path = row["host"].strip(), row["path"]
        if host and not HOST.fullmatch(host):
            out.append(f"Rule {n}: \"{host}\" is not a valid host name")
        kind = path.get("pathType")
        if kind not in PATH_TYPES:
            out.append(f"Rule {n}: the path type is {', '.join(PATH_TYPES)}")
        text = path.get("path", "")
        if kind in ("Prefix", "Exact") and not text.startswith("/"):
            out.append(f"Rule {n}: a {kind} path must start with /")
        backend = path.get("backend") or {}
        if "resource" not in backend:
            service = backend.get("service") or {}
            if not DNS_LABEL.fullmatch(service.get("name", "")):
                out.append(f"Rule {n}: enter the name of the service to send traffic to")
            port = (service.get("port") or {})
            value = port.get("number", port.get("name"))
            if value is None or not valid_port_or_name(value):
                out.append(f"Rule {n}: enter the service port (a number or a port name)")
        if (host, text) in seen:
            out.append(f"Rule {n}: {host or '*'}{text} is listed twice")
        seen.add((host, text))
    default = values["default_backend"]
    if default["service"].strip() and not default["service"].startswith("(resource"):
        if not DNS_LABEL.fullmatch(default["service"].strip()):
            out.append("Default backend: the service name must be lowercase letters, digits and '-'")
        if default["port"].strip() and not valid_port_or_name(port_value(default["port"])):
            out.append("Default backend: the port is a number or a port name")
    for n, tls in enumerate(values["tls"], 1):
        for host in tls.get("hosts") or []:
            if not HOST.fullmatch(host):
                out.append(f"TLS {n}: \"{host}\" is not a valid host name")
        secret = tls.get("secretName", "")
        if secret and not SUBDOMAIN.fullmatch(secret):
            out.append(f"TLS {n}: the secret name must be lowercase letters, digits, '-' and '.'")
    return out


# ---- NetworkPolicy ----------------------------------------------------------------------------------------
def policy_skeleton(namespace):
    """Starts as 'deny all incoming traffic to every pod': add rules to allow some."""
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "", "namespace": namespace}, "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}}


def peer_row(peer):
    """The table texts of a peer: namespace selector, pod selector, CIDR and the excepted ranges."""
    block = peer.get("ipBlock") or {}
    texts = {}
    for key, field in (("namespace", "namespaceSelector"), ("pod", "podSelector")):
        text = selector_text(peer.get(field)) if field in peer else ""
        if text is None:
            found = "; ".join(f"{e.get('key')} {e.get('operator')} {','.join(e.get('values') or [])}".strip()
                              for e in peer[field]["matchExpressions"])
            text = f"(expressions: {found})"
        texts[key] = text
    texts["cidr"] = block.get("cidr", "")
    texts["except"] = ",".join(block.get("except") or [])
    return texts


def build_peer(original, row):
    peer = deepcopy(original or {})
    for key, field in (("namespace", "namespaceSelector"), ("pod", "podSelector")):
        text = row[key].strip()
        if text.startswith("(expressions"):
            continue  # not touched: kept as it was
        selector, _problem = parse_selector(text)  # "all" gives {}, an empty text None
        if selector is None:
            peer.pop(field, None)
        else:
            peer[field] = selector
    cidr = row["cidr"].strip()
    if cidr:
        block = deepcopy(peer.get("ipBlock") or {})
        block["cidr"] = cidr
        excepts = [x.strip() for x in row["except"].split(",") if x.strip()]
        if excepts:
            block["except"] = excepts
        else:
            block.pop("except", None)
        peer["ipBlock"] = block
    else:
        peer.pop("ipBlock", None)
    return peer


def validate_peer(label, row):
    out = []
    for key, name in (("namespace", "namespace selector"), ("pod", "pod selector")):
        text = row[key].strip()
        if text and not text.startswith("(expressions"):
            _sel, problem = parse_selector(text)
            if problem:
                out.append(f"{label}: {name}: {problem}")
    cidr = row["cidr"].strip()
    if cidr and not valid_cidr(cidr):
        out.append(f"{label}: \"{cidr}\" is not a CIDR range such as 10.0.0.0/8")
    for item in (x.strip() for x in row["except"].split(",") if x.strip()):
        if not valid_cidr(item):
            out.append(f"{label}: the excepted range \"{item}\" is not a CIDR range")
    if cidr and (row["namespace"].strip() or row["pod"].strip()):
        out.append(f"{label}: an IP range cannot be combined with selectors in one entry; use a separate line")
    if row["except"].strip() and not cidr:
        out.append(f"{label}: excepted ranges need an IP range")
    if not (cidr or row["namespace"].strip() or row["pod"].strip()):
        out.append(f"{label}: fill in a selector or an IP range, or remove the line")
    return out


def validate_policy_ports(label, ports):
    out = []
    for n, p in enumerate(ports, 1):
        if p.get("protocol") not in (None, *PROTOCOLS):
            out.append(f"{label} port {n}: the protocol is {', '.join(PROTOCOLS)}")
        if "port" in p and not valid_port_or_name(p["port"]):
            out.append(f"{label} port {n}: the port is a number from 1 to 65535 or a port name")
        if "endPort" in p:
            if not valid_port(p["endPort"]):
                out.append(f"{label} port {n}: the end port must be a number from 1 to 65535")
            elif not isinstance(p.get("port"), int):
                out.append(f"{label} port {n}: a port range needs a numeric start port")
            elif p["endPort"] < p["port"]:
                out.append(f"{label} port {n}: the end port is lower than the start port")
    return out


def build_policy(raw, values, new=None):
    body = deepcopy(raw)
    if new:
        meta = body.setdefault("metadata", {})
        meta["name"], meta["namespace"] = new["name"].strip(), new["namespace"].strip()
    spec = body.setdefault("spec", {})
    selector = spec.get("podSelector") or {}
    _set_or_pop(selector, "matchLabels", dict(values["pod_selector"]))
    spec["podSelector"] = selector
    types = [t for t in POLICY_TYPES if values["types"].get(t)]
    derived = ["Ingress", *(["Egress"] if (raw.get("spec") or {}).get("egress") else [])]
    if "policyTypes" in spec or types != derived:
        spec["policyTypes"] = types
    for t, field in (("Ingress", "ingress"), ("Egress", "egress")):
        if values["types"].get(t):
            if values[field] or field in spec:
                spec[field] = deepcopy(values[field])
        else:
            spec.pop(field, None)
    _build_meta(body, values)
    return body


def policy_hints(values):
    out = []
    if values["types"].get("Ingress") and not values["ingress"]:
        out.append("Ingress is selected with no rules: all incoming traffic to the selected pods is blocked")
    if values["types"].get("Egress") and not values["egress"]:
        out.append("Egress is selected with no rules: the selected pods cannot send any traffic, not even DNS")
    if not values["pod_selector"]:
        out.append("No pod labels: the policy applies to every pod in the namespace")
    return out


def validate_policy(values):
    out = []
    if not any(values["types"].values()):
        out.append("Select Ingress, Egress or both")
    out += validate_pairs(values["pod_selector_pairs"], "pod selector label")
    return out + values.get("rule_problems", [])
