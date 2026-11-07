from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s import gateway as gw
from klotse.k8s.api_index import ApiResource
from klotse.k8s.client import KubeService, ReadOnlyError
from klotse.k8s.resources import custom_columns, map_custom_object
from klotse.ui.edit_dialog import EditDialog
from klotse.ui.navigator import Navigator
from klotse.ui.workspace import ClusterWorkspace

GATEWAY = {"metadata": {"name": "edge", "namespace": "infra", "uid": "g1"},
           "spec": {"gatewayClassName": "envoy", "listeners": [{"name": "http", "port": 80, "protocol": "HTTP"},
                                                              {"name": "https", "port": 443, "protocol": "HTTPS"}]},
           "status": {"addresses": [{"type": "IPAddress", "value": "10.0.0.5"}],
                      "conditions": [{"type": "Accepted", "status": "True"}, {"type": "Programmed", "status": "True"}]}}
ROUTE = {"metadata": {"name": "web", "namespace": "shop", "uid": "r1"},
         "spec": {"hostnames": ["app.example.com", "www.example.com"], "parentRefs": [{"name": "edge", "sectionName": "https"}, {"name": "other"}],
                  "rules": [{}, {}]},
         "status": {"parents": [{"conditions": [{"type": "Accepted", "status": "True"}]}, {"conditions": [{"type": "Accepted", "status": "False"}]}]}}
CLASS = {"metadata": {"name": "envoy"}, "spec": {"controllerName": "gateway.envoyproxy.io/controller", "description": "Envoy"},
         "status": {"conditions": [{"type": "Accepted", "status": "True"}]}}


def row(kind, item):
    return map_custom_object(item, namespaced=kind != "GatewayClass", group=gw.GROUP, kind=kind)


def test_rows_show_the_columns_that_matter():
    gateway = row("Gateway", GATEWAY)
    assert (gateway.gateway_class, gateway.listeners, gateway.addresses) == ("envoy", "http:80/HTTP, https:443/HTTPS", "10.0.0.5")
    assert gateway.ready == "✔ Programmed"
    route = row("HTTPRoute", ROUTE)
    assert (route.hostnames, route.parents, route.rules) == ("app.example.com, www.example.com", "edge/https, other", 2)
    assert route.accepted == "✖ Not accepted"  # one of its gateways refused it
    ok = {**ROUTE, "status": {"parents": [{"conditions": [{"type": "Accepted", "status": "True"}]}]}}
    assert row("GRPCRoute", ok).accepted == "✔ Accepted" and row("HTTPRoute", {"metadata": {"name": "x"}}).accepted == ""
    klass = row("GatewayClass", CLASS)
    assert klass.controller == "gateway.envoyproxy.io/controller" and klass.accepted == "✔ Accepted"
    assert [h for h, _ in custom_columns(gw.GROUP, "HTTPRoute")][:4] == ["Name", "Namespace", "Hostnames", "Gateways"]
    assert row("Gateway", {"metadata": {"name": "bare"}}).listeners == ""  # an odd object never breaks the list


def test_skeletons_and_ordering():
    assert gw.skeleton("GatewayClass")["metadata"] == {"name": ""} and "namespace" not in gw.skeleton("GatewayClass")["metadata"]
    gateway = gw.skeleton("Gateway", "infra", "v1")
    assert gateway["apiVersion"] == "gateway.networking.k8s.io/v1" and gateway["metadata"]["namespace"] == "infra"
    assert gateway["spec"]["listeners"][0]["port"] == 80
    route = gw.skeleton("HTTPRoute", "shop")
    assert route["spec"]["rules"][0]["matches"][0]["path"]["value"] == "/" and "matches" not in gw.skeleton("GRPCRoute")["spec"]["rules"][0]
    assert gw.skeleton("ReferenceGrant")["spec"] == {}
    kinds = [SimpleNamespace(kind=k) for k in ("TLSRoute", "HTTPRoute", "Gateway", "ReferenceGrant", "GatewayClass", "GRPCRoute")]
    assert [r.kind for r in gw.order(kinds)] == ["GatewayClass", "Gateway", "HTTPRoute", "GRPCRoute", "ReferenceGrant", "TLSRoute"]


def test_discovery_only_looks_at_the_gateway_group():
    service = KubeService.__new__(KubeService)
    service.discovery_groups = lambda: [("", "v1"), ("apps", "v1"), (gw.GROUP, "v1")]
    asked = []

    def resources(group, version):
        asked.append(group)
        return [ApiResource(group, "gateways", "Gateway", True, version), ApiResource(group, "gateways/status", "Gateway", True, version),
                ApiResource(group, "httproutes", "HTTPRoute", True, version), ApiResource(group, "gatewayclasses", "GatewayClass", False, version)]
    service.discovery_resources = resources
    found = service.gateway_resources()
    assert asked == [gw.GROUP] and [r.kind for r in found] == ["GatewayClass", "Gateway", "HTTPRoute"]  # no subresource, well-known order
    service.discovery_groups = lambda: [("", "v1")]
    assert service.gateway_resources() == []


def test_custom_object_calls_use_the_right_api_and_respect_read_only():
    calls = []
    custom = SimpleNamespace(**{name: (lambda n=name: lambda *a, **k: calls.append((n, a, k)))() for name in (
        "create_namespaced_custom_object", "create_cluster_custom_object", "replace_namespaced_custom_object",
        "replace_cluster_custom_object", "delete_namespaced_custom_object", "delete_cluster_custom_object")})
    service = KubeService.__new__(KubeService)
    service.read_only = False
    service._custom = custom
    service.create_custom(gw.GROUP, "v1", "gateways", True, {"metadata": {"name": "x", "namespace": "infra"}})
    service.create_custom(gw.GROUP, "v1", "gatewayclasses", False, {"metadata": {"name": "c"}})
    service.replace_custom(gw.GROUP, "v1", "gateways", True, "infra", "x", {"a": 1})
    service.replace_custom(gw.GROUP, "v1", "gatewayclasses", False, "", "c", {"a": 1})
    service.delete_custom(gw.GROUP, "v1", "gateways", True, "infra", "x", "uid-1")
    service.delete_custom(gw.GROUP, "v1", "gatewayclasses", False, "", "c", "uid-2")
    assert [c[0] for c in calls] == ["create_namespaced_custom_object", "create_cluster_custom_object", "replace_namespaced_custom_object",
                                     "replace_cluster_custom_object", "delete_namespaced_custom_object", "delete_cluster_custom_object"]
    assert calls[0][1][:3] == (gw.GROUP, "v1", "infra") and calls[4][2]["body"].preconditions.uid == "uid-1"
    with pytest.raises(ValueError, match="UID"):
        service.delete_custom(gw.GROUP, "v1", "gateways", True, "infra", "x", "")
    service.read_only = True
    for call in (lambda: service.create_custom(gw.GROUP, "v1", "gateways", True, {}),
                 lambda: service.replace_custom(gw.GROUP, "v1", "gateways", True, "n", "x", {}),
                 lambda: service.delete_custom(gw.GROUP, "v1", "gateways", True, "n", "x", "u")):
        with pytest.raises(ReadOnlyError):
            call()
    assert len(calls) == 6  # nothing more was sent


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def resources():
    return [ApiResource(gw.GROUP, "gatewayclasses", "GatewayClass", False, "v1"), ApiResource(gw.GROUP, "gateways", "Gateway", True, "v1"),
            ApiResource(gw.GROUP, "httproutes", "HTTPRoute", True, "v1")]


def test_found_kinds_become_editable_lists_and_the_navigator_gets_a_group(qtbot):
    ws = make_ws(qtbot)
    got = []
    ws.gateway_found.connect(got.append)
    ws._gateway_found(resources())
    labels = [label for _, label in got[0]]
    assert labels == ["GatewayClasses", "Gateways", "HTTPRoutes"] and set(ws._custom_kinds) == {"GatewayClass", "Gateway", "HTTPRoute"}
    table = ws.views[got[0][1][0]][0]
    assert table.kind == "Gateway" and table.editable and table.deletable and table.new_button is not None
    assert ws._custom_kinds["Gateway"] == (gw.GROUP, "v1", "gateways", True)
    ws._gateway_found([])
    assert len(got) == 1  # nothing found: nothing shown
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["ctx"])
    nav.set_state("ctx", "connected")
    nav.show_views("ctx")
    nav.show_gateway("ctx", got[0])
    item = nav.cluster_item("ctx")
    names = [item.child(i).text(0) for i in range(item.childCount())]
    assert "Gateway API" in names
    nav.show_gateway("ctx", got[0])  # replaced, not duplicated
    assert [item.child(i).text(0) for i in range(item.childCount())].count("Gateway API") == 1


def test_edit_new_and_delete_go_through_the_custom_object_calls(qtbot, monkeypatch):
    calls = []
    service = dict(create_custom=lambda *a: calls.append(("create", *a)), replace_custom=lambda *a: calls.append(("replace", *a)),
                   delete_custom=lambda *a: calls.append(("delete", *a)))
    ws = make_ws(qtbot, **service)
    ws._gateway_found(resources())
    seen = {}

    def exec_(self):
        seen["title"] = self.windowTitle()
        self.result_body = seen.setdefault("body", None) or {"metadata": {"name": "edge", "namespace": "infra"}, "spec": {"x": 1}}
        return QDialog.Accepted
    monkeypatch.setattr(EditDialog, "exec", exec_)
    gateway_row = SimpleNamespace(name="edge", namespace="infra", raw=GATEWAY)
    ws.edit_resource("Gateway", gateway_row)
    ws.create_resource("Gateway")
    ws.create_resource("GatewayClass")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.delete_resource("Gateway", gateway_row)
    qtbot.waitUntil(lambda: len(calls) == 4, timeout=5000)
    by_kind = {c[0]: c for c in calls}
    assert by_kind["replace"][1:] == (gw.GROUP, "v1", "gateways", True, "infra", "edge", {"metadata": {"name": "edge", "namespace": "infra"}, "spec": {"x": 1}})
    assert by_kind["delete"][1:] == (gw.GROUP, "v1", "gateways", True, "infra", "edge", "g1")
    creates = [c for c in calls if c[0] == "create"]
    assert {c[3] for c in creates} == {"gateways", "gatewayclasses"}
    qtbot.waitUntil(lambda: not ws._workers)


def test_new_starts_from_the_skeleton_in_the_chosen_namespace(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws._gateway_found(resources())
    ws.set_namespace("All namespaces")
    shown = []
    monkeypatch.setattr(ws, "_edit_flow", lambda kind, raw, name, namespace, creating=False, secret_data=None: shown.append((kind, raw, creating)))
    ws.create_resource("HTTPRoute")
    kind, raw, creating = shown[0]
    assert kind == "HTTPRoute" and creating and raw["kind"] == "HTTPRoute" and raw["metadata"]["namespace"] == "default"
    assert raw["spec"]["parentRefs"][0]["name"] == "my-gateway"


def test_a_read_only_cluster_refuses_all_three(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, create_custom=lambda *a: calls.append(a), replace_custom=lambda *a: calls.append(a),
                 delete_custom=lambda *a: calls.append(a))
    ws._gateway_found(resources())
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    opened = []
    monkeypatch.setattr(EditDialog, "exec", lambda self: opened.append(1) or QDialog.Rejected)
    ws.set_read_only(True)
    gateway_row = SimpleNamespace(name="edge", namespace="infra", raw=GATEWAY)
    ws.edit_resource("Gateway", gateway_row)
    ws.create_resource("Gateway")
    ws.delete_resource("Gateway", gateway_row)
    assert calls == [] and opened == []
