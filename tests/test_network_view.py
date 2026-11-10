from types import SimpleNamespace

import pytest
from PySide6.QtGui import QTextDocument

from klotse.ui import network_view as nv, theme
from klotse.ui.detail_panel import DetailPanel


def text(kind, raw):
    doc = QTextDocument()
    doc.setHtml(nv.render(kind, raw))
    return doc.toPlainText()


def test_service_addresses_ports_and_escaped_selectors(qapp):
    raw = {"metadata": {"name": "front"}, "spec": {"type": "LoadBalancer", "clusterIPs": ["10.0.0.1", "fd00::1"],
        "selector": {"app": "<script>"}, "ports": [{"name": "web", "port": 80, "targetPort": "http", "nodePort": 31000}]},
        "status": {"loadBalancer": {"ingress": [{"hostname": "lb.example"}]}}}
    page = nv.render("Service", raw)
    assert "<script>" not in page and "&lt;script&gt;" in page
    result = text("Service", raw)
    for value in ("LoadBalancer", "10.0.0.1, fd00::1", "80/TCP", "http", "31000", "lb.example"):
        assert value in result


def test_ingress_v1_and_legacy_backends_tls_and_resource_refs(qapp):
    raw = {"metadata": {"namespace": "shop"}, "spec": {"ingressClassName": "alb", "defaultBackend": {
        "service": {"name": "default", "port": {"number": 80}}}, "rules": [{"host": "shop.example", "http": {
        "paths": [{"path": "/api", "pathType": "Prefix", "backend": {"service": {"name": "api", "port": {"name": "http"}}}},
                  {"path": "/old", "backend": {"serviceName": "legacy", "servicePort": 8080}}]}}],
        "tls": [{"hosts": ["shop.example"], "secretName": "shop-cert"}]}}
    result = text("Ingress", raw)
    for value in ("alb", "default : 80", "shop.example", "/api", "Prefix", "api : http", "legacy : 8080", "shop-cert"):
        assert value in result
    assert "klotse-goto:" in nv.render("Ingress", raw)


def test_endpoints_and_slices_readiness_and_missing_conditions(qapp):
    result = text("Endpoints", {"subsets": [{"addresses": [{"ip": "10.1.1.1", "targetRef": {"kind": "Pod", "name": "p"}}],
        "notReadyAddresses": [{"ip": "10.1.1.2"}], "ports": [{"port": 8080}]}]})
    assert "10.1.1.1" in result and "Not ready" in result and "8080/TCP" in result
    result = text("EndpointSlice", {"addressType": "IPv6", "endpoints": [
        {"addresses": ["fd00::1"], "conditions": {"ready": False, "terminating": True}}, {"addresses": ["fd00::2"]}]})
    for value in ("IPv6", "fd00::1", "Not ready", "Terminating", "Unknown"):
        assert value in result


def test_endpoint_lists_are_bounded(qapp):
    raw = {"endpoints": [{"addresses": [f"addr-{i}"]} for i in range(100)]}
    result = text("EndpointSlice", raw)
    assert "addr-79" in result and "addr-99" not in result
    assert "first 80" in result


def test_policy_defaulting_and_empty_rule_semantics(qapp):
    denied = text("NetworkPolicy", {"spec": {"podSelector": {}}})
    assert "No traffic allowed by this policy" in denied
    assert "Not restricted by this policy" in denied  # Egress is not selected by default
    allowed = text("NetworkPolicy", {"spec": {"ingress": [{}], "egress": [{}]}})
    assert "All peers" in allowed and "All ports" in allowed
    assert "No traffic allowed" not in allowed


def test_policy_peers_are_and_within_peer_or_between_peers(qapp):
    raw = {"spec": {"podSelector": {"matchLabels": {"app": "api"}}, "policyTypes": ["Ingress"], "ingress": [{
        "from": [{"namespaceSelector": {}, "podSelector": {"matchLabels": {"app": "caller"}}},
                 {"ipBlock": {"cidr": "10.0.0.0/8", "except": ["10.1.0.0/16"]}}],
        "ports": [{"port": 8000, "endPort": 8099, "protocol": "TCP"}]}]}}
    result = text("NetworkPolicy", raw)
    assert "namespaces: all AND pods: app=caller OR 10.0.0.0/8 except 10.1.0.0/16" in result
    assert "8000–8099/TCP" in result


@pytest.mark.parametrize("kind", nv.KINDS)
def test_missing_optional_fields_and_panel_dispatch(qtbot, kind):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(SimpleNamespace(table_kind=kind, name="network", namespace="shop", raw={}, secret_data={}))
    assert panel._formatted_ok and panel.formatted.isVisible()
    assert panel.formatted_button.isEnabled() and not panel.editor.isVisible()


@pytest.mark.parametrize("version", ["v1", "events.k8s.io/v1"])
@pytest.mark.parametrize("event_type", ["Normal", "Warning", None])
def test_event_identity_status_uses_type(qtbot, version, event_type):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    raw = {"kind": "Event", "apiVersion": version, "metadata": {"name": "event"}}
    if event_type:
        raw["type"] = event_type
    panel.show_row(SimpleNamespace(table_kind="Event", name="event", namespace="shop", raw=raw, secret_data={}))
    assert panel.identity_status.text() == (event_type or "Unknown")
    if event_type == "Warning":
        assert theme.WARN in panel.identity_status.styleSheet()
    assert panel.yaml_button.isChecked()
