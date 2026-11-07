"""Gateway API (gateway.networking.k8s.io): which kinds Klotse lists and edits, and the starting manifests for New… (pure).

Klotse does not install anything and does not need the Gateway API to be present: the group is looked for in the cluster's API
discovery, and the navigator shows a **Gateway API** group only when it is served (like Karpenter). The instances are edited as
YAML (there is no form editor for them yet)."""

GROUP = "gateway.networking.k8s.io"
FIRST = ("GatewayClass", "Gateway", "HTTPRoute", "GRPCRoute")  # listed first, in this order; any other kind of the group follows
CLUSTER_SCOPED = ("GatewayClass",)


def is_gateway_group(group):
    return group == GROUP


def skeleton(kind, namespace="default", version="v1"):
    """The manifest New… starts from for a Gateway API kind. Not applied until the user presses Create; the values are examples to
    be changed (the class name and the parent gateway have to exist in the cluster)."""
    api = f"{GROUP}/{version}"
    if kind == "GatewayClass":
        return {"apiVersion": api, "kind": kind, "metadata": {"name": ""}, "spec": {"controllerName": "example.com/gateway-controller"}}
    meta = {"name": "", "namespace": namespace}
    if kind == "Gateway":
        return {"apiVersion": api, "kind": kind, "metadata": meta,
                "spec": {"gatewayClassName": "example-class",
                         "listeners": [{"name": "http", "port": 80, "protocol": "HTTP", "allowedRoutes": {"namespaces": {"from": "Same"}}}]}}
    if kind in ("HTTPRoute", "GRPCRoute"):
        rule = {"backendRefs": [{"name": "my-service", "port": 80}]}
        if kind == "HTTPRoute":
            rule["matches"] = [{"path": {"type": "PathPrefix", "value": "/"}}]
        return {"apiVersion": api, "kind": kind, "metadata": meta,
                "spec": {"parentRefs": [{"name": "my-gateway"}], "hostnames": ["app.example.com"], "rules": [rule]}}
    return {"apiVersion": api, "kind": kind, "metadata": meta, "spec": {}}


def order(resources):
    """Kinds of the group in the order the navigator lists them: the well-known four first, the rest by name."""
    rank = {kind: i for i, kind in enumerate(FIRST)}
    return sorted(resources, key=lambda r: (rank.get(r.kind, len(FIRST)), r.kind))
