"""Service, Ingress and NetworkPolicy forms: round trips, edits, validation, creating, and the New buttons."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui import network_model as nm
from klotse.ui.network_editor import NetworkEditDialog, new_manifest
from klotse.ui.workspace import ClusterWorkspace

SERVICE = {"apiVersion": "v1", "kind": "Service",
           "metadata": {"name": "web", "namespace": "shop", "uid": "u", "resourceVersion": "9", "labels": {"app": "web"}},
           "spec": {"type": "LoadBalancer", "clusterIP": "10.0.0.5", "clusterIPs": ["10.0.0.5"], "ipFamilies": ["IPv4"],
                    "selector": {"app": "web"}, "sessionAffinity": "None", "externalTrafficPolicy": "Cluster",
                    "internalTrafficPolicy": "Cluster", "loadBalancerSourceRanges": ["10.0.0.0/8"],
                    "ports": [{"name": "http", "port": 80, "targetPort": 8080, "protocol": "TCP", "nodePort": 31080},
                              {"name": "metrics", "port": 9090, "targetPort": "metrics", "protocol": "TCP", "nodePort": 31090}]},
           "status": {"loadBalancer": {"ingress": [{"ip": "1.2.3.4"}]}}}
INGRESS = {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
           "metadata": {"name": "site", "namespace": "shop", "annotations": {"nginx.ingress.kubernetes.io/ssl-redirect": "true"}},
           "spec": {"ingressClassName": "nginx",
                    "defaultBackend": {"service": {"name": "fallback", "port": {"number": 80}}},
                    "tls": [{"hosts": ["a.example.com", "b.example.com"], "secretName": "site-tls"}],
                    "rules": [{"host": "a.example.com", "http": {"paths": [
                        {"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "web", "port": {"number": 80}}}},
                        {"path": "/api", "pathType": "Exact", "backend": {"service": {"name": "api", "port": {"name": "http"}}}},
                        {"path": "/img", "pathType": "Prefix", "backend": {"resource": {"apiGroup": "k8s.example.com", "kind": "Bucket", "name": "img"}}}]}},
                              {"host": "b.example.com", "http": {"paths": [
                                  {"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "web", "port": {"number": 80}}}}]}},
                              {"host": "empty.example.com"}]},
           "status": {"loadBalancer": {}}}
POLICY = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "db-only", "namespace": "shop"},
          "spec": {"podSelector": {"matchLabels": {"app": "db"}}, "policyTypes": ["Ingress", "Egress"],
                   "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "web"}}},
                                         {"namespaceSelector": {"matchLabels": {"team": "a"}}, "podSelector": {"matchLabels": {"app": "x"}}},
                                         {"podSelector": {"matchExpressions": [{"key": "tier", "operator": "In", "values": ["a"]}]}},
                                         {"ipBlock": {"cidr": "10.0.0.0/8", "except": ["10.1.0.0/16"]}}],
                                "ports": [{"protocol": "TCP", "port": 5432}, {"protocol": "TCP", "port": 6000, "endPort": 6010}]}],
                   "egress": [{"to": [{"namespaceSelector": {}}], "ports": [{"protocol": "UDP", "port": 53}]}, {}]}}


def make(qtbot, kind, raw, create=False):
    dlg = NetworkEditDialog(kind, deepcopy(raw), create=create, namespaces=["shop"])
    qtbot.addWidget(dlg)
    return dlg


def saved(dlg):
    dlg._save()
    assert dlg.result_body is not None, dlg.hint.text()
    return dlg.result_body


# ---- helpers -------------------------------------------------------------------------------------
def test_port_and_cidr_helpers():
    assert nm.valid_port(80) and not nm.valid_port(0) and not nm.valid_port(70000) and not nm.valid_port("80")
    assert nm.valid_port_or_name("http") and nm.valid_port_or_name(8080) and not nm.valid_port_or_name("123")
    assert not nm.valid_port_or_name("Has_Caps") and not nm.valid_port_or_name("x" * 16)
    assert nm.port_value("80") == 80 and nm.port_value("http") == "http"
    assert nm.valid_cidr("10.0.0.0/8") and nm.valid_cidr("fd00::/8") and not nm.valid_cidr("10.0.0.1") and not nm.valid_cidr("nope")


def test_selectors_as_text():
    assert nm.selector_text(None) == "" and nm.selector_text({}) == "all"
    assert nm.selector_text({"matchLabels": {"a": "1", "b": "2"}}) == "a=1,b=2"
    assert nm.selector_text({"matchExpressions": [{"key": "k"}]}) is None
    assert nm.parse_selector("") == (None, "") and nm.parse_selector("all") == ({}, "")
    assert nm.parse_selector("a=1, b=2") == ({"matchLabels": {"a": "1", "b": "2"}}, "")
    assert nm.parse_selector("bad")[1] and nm.parse_selector("=x")[1]


# ---- Service -----------------------------------------------------------------------------------------
def test_untouched_objects_come_back_identical(qtbot):
    for kind, raw in (("Service", SERVICE), ("Ingress", INGRESS), ("NetworkPolicy", POLICY)):
        assert saved(make(qtbot, kind, raw)) == raw, kind


def test_a_service_edit_changes_only_its_fields(qtbot):
    dlg = make(qtbot, "Service", SERVICE)
    assert dlg.type_box.currentText() == "LoadBalancer" and dlg.ports.count() == 2
    dlg.ports.table.item(0, 2).setText("9000")
    dlg.ports.table.item(1, 2).setText("metrics2")
    dlg.ext_policy.setCurrentIndex(2)
    dlg.affinity.setCurrentText("ClientIP")
    dlg.affinity_timeout.setText("600")
    dlg.selector.key_box.setEditText("tier")
    dlg.selector.value_edit.setText("front")
    dlg.selector._add()
    spec = saved(dlg)["spec"]
    assert spec["ports"][0] == {"name": "http", "port": 80, "targetPort": 9000, "protocol": "TCP", "nodePort": 31080}
    assert spec["ports"][1]["targetPort"] == "metrics2"
    assert spec["externalTrafficPolicy"] == "Local" and spec["sessionAffinity"] == "ClientIP"
    assert spec["sessionAffinityConfig"] == {"clientIP": {"timeoutSeconds": 600}} and spec["selector"] == {"app": "web", "tier": "front"}
    assert spec["clusterIP"] == "10.0.0.5" and spec["ipFamilies"] == ["IPv4"]  # not in the form: untouched


def test_changing_the_type_cleans_up_what_no_longer_applies(qtbot):
    dlg = make(qtbot, "Service", SERVICE)
    dlg.type_box.setCurrentText("ClusterIP")
    assert not dlg.source_ranges.isEnabled() and not dlg.ext_policy.isEnabled()
    spec = saved(dlg)["spec"]
    assert spec["type"] == "ClusterIP" and all("nodePort" not in p for p in spec["ports"])
    assert "externalTrafficPolicy" not in spec and "loadBalancerSourceRanges" not in spec
    ext = make(qtbot, "Service", SERVICE)
    ext.type_box.setCurrentText("ExternalName")
    ext.external_name.setText("db.example.com")
    spec = saved(ext)["spec"]
    assert spec["externalName"] == "db.example.com" and "selector" not in spec and not ext.selector.isEnabled()


@pytest.mark.parametrize("change, message", [
    (lambda d: d.ports.table.item(0, 1).setText("70000"), "from 1 to 65535"),
    (lambda d: d.ports.table.item(0, 2).setText("Bad_Name"), "target port"),
    (lambda d: d.ports.table.item(1, 0).setText(""), "needs a name"),
    (lambda d: d.ports.table.item(1, 1).setText("80"), "listed twice"),
    (lambda d: d.ports.table.item(0, 4).setText("0"), "node port"),
    (lambda d: d.affinity_timeout.setText("100000"), "Session timeout"),
    (lambda d: d.source_ranges.setPlainText("10.0.0.1"), "CIDR"),
])
def test_service_validation(qtbot, monkeypatch, change, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot, "Service", SERVICE)
    change(dlg)
    assert any(message in p for p in dlg.problems())
    dlg._save()
    assert dlg.result_body is None


def test_a_new_service_and_its_hints(qtbot):
    dlg = make(qtbot, "Service", new_manifest("Service", "shop"), create=True)
    assert dlg.windowTitle() == "New Service" and dlg.namespace_edit.currentText() == "shop"
    assert dlg.problems() == ["Enter a name"] and "No selector" in "".join(nm.service_hints(dlg.values()))
    dlg.name_edit.setText("api")
    dlg.cluster_ip.setText("None")
    dlg.selector.key_box.setEditText("app")
    dlg.selector.value_edit.setText("api")
    dlg.selector._add()
    body = saved(dlg)
    assert body["metadata"] == {"name": "api", "namespace": "shop"}
    assert body["spec"]["clusterIP"] == "None" and body["spec"]["selector"] == {"app": "api"} and "type" in body["spec"]
    bad = make(qtbot, "Service", new_manifest("Service", "shop"), create=True)
    bad.name_edit.setText("api")
    bad.cluster_ip.setText("not-an-ip")
    assert any("Cluster IP" in p for p in bad.problems())


def test_a_load_balancer_gets_a_cost_hint(qtbot):
    assert any("costs money" in h for h in nm.service_hints(make(qtbot, "Service", SERVICE).values()))


# ---- Ingress ---------------------------------------------------------------------------------------------
def test_an_ingress_shows_one_row_per_path_and_keeps_resource_backends(qtbot):
    dlg = make(qtbot, "Ingress", INGRESS)
    rows = dlg.rules.texts()
    assert [(r["host"], r["path"], r["service"], r["port"]) for r in rows] == [
        ("a.example.com", "/", "web", "80"), ("a.example.com", "/api", "api", "http"),
        ("a.example.com", "/img", "(resource Bucket/img)", ""), ("b.example.com", "/", "web", "80")]
    assert dlg.ingress_class.currentText() == "nginx" and dlg.default_service.currentText() == "fallback"
    assert dlg.tls.texts() == [{"hosts": "a.example.com, b.example.com", "secretName": "site-tls"}]


def test_an_ingress_edit_regroups_by_host_and_keeps_the_rest(qtbot):
    dlg = make(qtbot, "Ingress", INGRESS)
    dlg.rules.table.item(0, 3).setText("web2")
    dlg.rules.table.item(1, 4).setText("8080")
    dlg.rules._add()
    last = dlg.rules.count() - 1
    for col, text in enumerate(["b.example.com", "/docs", "Exact", "docs", "80"]):
        dlg.rules.table.item(last, col).setText(text)
    dlg.default_service.setCurrentText("")
    dlg.tls.table.item(0, 0).setText("a.example.com")
    spec = saved(dlg)["spec"]
    a, b, empty = spec["rules"]
    assert a["host"] == "a.example.com" and a["http"]["paths"][0]["backend"]["service"]["name"] == "web2"
    assert a["http"]["paths"][1]["backend"]["service"]["port"] == {"number": 8080}
    assert a["http"]["paths"][2] == INGRESS["spec"]["rules"][0]["http"]["paths"][2]  # the resource backend, untouched
    assert [p["path"] for p in b["http"]["paths"]] == ["/", "/docs"] and b["http"]["paths"][1]["pathType"] == "Exact"
    assert empty == {"host": "empty.example.com"} and "defaultBackend" not in spec
    assert spec["tls"] == [{"hosts": ["a.example.com"], "secretName": "site-tls"}]


@pytest.mark.parametrize("change, message", [
    (lambda d: d.rules.table.item(0, 0).setText("Bad Host"), "valid host"),
    (lambda d: d.rules.table.item(0, 1).setText("nope"), "must start with /"),
    (lambda d: d.rules.table.item(0, 2).setText("Fuzzy"), "path type"),
    (lambda d: d.rules.table.item(0, 3).setText(""), "name of the service"),
    (lambda d: d.rules.table.item(0, 4).setText(""), "service port"),
    (lambda d: d.rules.table.item(1, 1).setText("/"), "listed twice"),
    (lambda d: d.tls.table.item(0, 0).setText("bad host"), "valid host"),
    (lambda d: d.tls.table.item(0, 1).setText("Bad_Secret"), "secret name"),
    (lambda d: d.default_port.setText("0"), "Default backend"),
])
def test_ingress_validation(qtbot, monkeypatch, change, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot, "Ingress", INGRESS)
    change(dlg)
    assert any(message in p for p in dlg.problems()), dlg.problems()
    dlg._save()
    assert dlg.result_body is None


def test_a_new_ingress_starts_with_one_rule_to_fill_in(qtbot):
    dlg = make(qtbot, "Ingress", new_manifest("Ingress", "shop"), create=True)
    dlg.name_edit.setText("site")
    assert any("name of the service" in p for p in dlg.problems())
    dlg.rules.table.item(0, 0).setText("shop.example.com")
    dlg.rules.table.item(0, 3).setText("web")
    body = saved(dlg)
    assert body["spec"]["rules"] == [{"host": "shop.example.com", "http": {"paths": [
        {"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "web", "port": {"number": 80}}}}]}}]
    assert "ingressClassName" not in body["spec"] and "tls" not in body["spec"]


# ---- NetworkPolicy ---------------------------------------------------------------------------------------
def test_a_policy_shows_peers_and_ports_per_rule(qtbot):
    dlg = make(qtbot, "NetworkPolicy", POLICY)
    assert all(b.isChecked() for b in dlg.type_boxes.values())
    peers = dlg.ingress.items[0]["peers"].texts()
    assert peers[0] == {"namespace": "", "pod": "app=web", "cidr": "", "except": ""}
    assert peers[1] == {"namespace": "team=a", "pod": "app=x", "cidr": "", "except": ""}
    assert peers[2]["pod"].startswith("(expressions") and peers[3] == {"namespace": "", "pod": "", "cidr": "10.0.0.0/8", "except": "10.1.0.0/16"}
    assert dlg.egress.items[0]["peers"].texts()[0]["namespace"] == "all"
    assert [dlg.egress.list.item(i).text() for i in range(2)] == ["Rule 1: 1 peer(s), 1 port(s)", "Rule 2: anywhere, any port"]


def test_a_policy_edit_keeps_expressions_and_empty_rules(qtbot):
    dlg = make(qtbot, "NetworkPolicy", POLICY)
    rule = dlg.ingress.items[0]
    rule["peers"].table.item(0, 1).setText("app=frontend")
    rule["peers"].table.item(1, 0).setText("")
    rule["peers"].table.item(3, 3).setText("")
    rule["ports"].table.item(0, 1).setText("5433")
    dlg.pod_selector.key_box.setEditText("tier")
    dlg.pod_selector.value_edit.setText("data")
    dlg.pod_selector._add()
    spec = saved(dlg)["spec"]
    peers = spec["ingress"][0]["from"]
    assert peers[0] == {"podSelector": {"matchLabels": {"app": "frontend"}}} and "namespaceSelector" not in peers[1]
    assert peers[2] == POLICY["spec"]["ingress"][0]["from"][2]  # expressions are kept as they were
    assert peers[3] == {"ipBlock": {"cidr": "10.0.0.0/8"}}
    assert spec["ingress"][0]["ports"][0] == {"protocol": "TCP", "port": 5433}
    assert spec["ingress"][0]["ports"][1] == {"protocol": "TCP", "port": 6000, "endPort": 6010}
    assert spec["egress"][1] == {} and spec["podSelector"] == {"matchLabels": {"app": "db", "tier": "data"}}


def test_adding_and_removing_rules_and_directions(qtbot):
    dlg = make(qtbot, "NetworkPolicy", POLICY)
    dlg.ingress.add_button.click()
    assert len(dlg.ingress.items) == 2 and dlg.ingress.items[1]["peers"].texts()[0]["pod"] == "all"
    dlg.egress.list.setCurrentRow(1)
    dlg.egress.remove_button.click()
    dlg.type_boxes["Egress"].setChecked(False)
    assert not dlg.egress.isEnabled()
    spec = saved(dlg)["spec"]
    assert spec["policyTypes"] == ["Ingress"] and "egress" not in spec
    assert spec["ingress"][1] == {"from": [{"podSelector": {}}]}


def test_a_new_policy_is_deny_all_ingress_and_says_so(qtbot):
    dlg = make(qtbot, "NetworkPolicy", new_manifest("NetworkPolicy", "shop"), create=True)
    assert "deny all incoming traffic" in dlg.banner.text()
    dlg.name_edit.setText("deny-all")
    hints = nm.policy_hints(dlg.values())
    assert any("all incoming traffic" in h for h in hints) and any("every pod" in h for h in hints)
    body = saved(dlg)
    assert body["spec"] == {"podSelector": {}, "policyTypes": ["Ingress"]}


def test_egress_without_rules_warns_about_dns(qtbot):
    dlg = make(qtbot, "NetworkPolicy", new_manifest("NetworkPolicy", "shop"), create=True)
    dlg.type_boxes["Egress"].setChecked(True)
    assert any("not even DNS" in h for h in nm.policy_hints(dlg.values()))


@pytest.mark.parametrize("change, message", [
    (lambda d: d.ingress.items[0]["peers"].table.item(0, 1).setText("nokey"), "not key=value"),
    (lambda d: d.ingress.items[0]["peers"].table.item(3, 2).setText("10.0.0.1"), "CIDR"),
    (lambda d: d.ingress.items[0]["peers"].table.item(3, 3).setText("bad"), "excepted range"),
    (lambda d: d.ingress.items[0]["peers"].table.item(0, 2).setText("10.0.0.0/8"), "cannot be combined"),
    (lambda d: d.ingress.items[0]["ports"].table.item(0, 1).setText("0"), "from 1 to 65535"),
    (lambda d: d.ingress.items[0]["ports"].table.item(1, 2).setText("5000"), "lower than the start"),
    (lambda d: (d.ingress.items[0]["ports"].table.item(0, 1).setText("http"),
                d.ingress.items[0]["ports"].table.item(0, 2).setText("6000")), "range needs"),
    (lambda d: [b.setChecked(False) for b in d.type_boxes.values()], "Select Ingress"),
])
def test_policy_validation(qtbot, monkeypatch, change, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot, "NetworkPolicy", POLICY)
    change(dlg)
    assert any(message in p for p in dlg.problems()), dlg.problems()
    dlg._save()
    assert dlg.result_body is None


# ---- hand-over, workspace, service --------------------------------------------------------------------------
def test_the_forms_hand_pending_changes_to_yaml(qtbot):
    dlg = make(qtbot, "Service", SERVICE)
    dlg.type_box.setCurrentText("NodePort")
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.pending["spec"]["type"] == "NodePort"


@pytest.mark.parametrize("key, kind", [("services", "Service"), ("ingresses", "Ingress"), ("networkpolicies", "NetworkPolicy")])
def test_the_lists_have_a_new_button_and_use_the_form(qtbot, key, kind):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    table = ws.views[key][0]
    assert table.kind == kind and table.new_button is not None


def test_new_and_edit_route_to_the_network_dialog(qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from klotse.k8s.resources import Row

    created, replaced, seen = [], [], []
    ws = ClusterWorkspace(SimpleNamespace(create=lambda k, b: created.append((k, b)), replace=lambda k, b: replaced.append((k, b)), list_ingress_class_names=lambda: [], list_pod_labels=lambda ns: [], list_service_names=lambda ns: []), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    ws.set_namespace("default")

    class Form:
        def __init__(self, kind, raw, parent=None, create=False, namespaces=(), **_kw):
            seen.append((kind, create, raw["metadata"]["namespace"]))
            self.wants_yaml, self.result_body = False, {"metadata": {"name": "n", "namespace": "default"}}

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.NetworkEditDialog", Form)
    ws.create_resource("Ingress")
    ws.edit_resource("NetworkPolicy", Row("p", "shop", POLICY))
    qtbot.waitUntil(lambda: bool(created) and bool(replaced), timeout=3000)
    assert seen == [("Ingress", True, "default"), ("NetworkPolicy", False, "shop")]
    assert created[0][0] == "Ingress" and replaced[0][0] == "NetworkPolicy"


def test_the_service_creates_services_ingresses_and_policies():
    calls = []
    meta = SimpleNamespace(name="x", namespace="ns", uid="u", creation_timestamp=None, labels=None, annotations=None)
    obj = SimpleNamespace(metadata=meta)
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(create_namespaced_service=lambda ns, body: calls.append(("service", ns)) or obj)
    svc._apis = {"net": SimpleNamespace(create_namespaced_ingress=lambda ns, body: calls.append(("ingress", ns)) or obj,
                                        create_namespaced_network_policy=lambda ns, body: calls.append(("policy", ns)) or obj)}
    for kind in ("Service", "Ingress", "NetworkPolicy"):
        try:
            svc.create(kind, {"metadata": {"name": "n", "namespace": "ns"}})
        except AttributeError:
            pass  # the fake object lacks fields the row mapper reads; the API call is what matters
    assert calls == [("service", "ns"), ("ingress", "ns"), ("policy", "ns")]


# ---- ingress classes ---------------------------------------------------------------------------------------
class ClassList:
    """Stands in for IngressClassDirectory."""

    def __init__(self, entries, state="done"):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            finished = Signal()
        self._e = Emitter()
        self.finished = self._e.finished
        self.entries, self.state, self.error = entries, state, ""

    names = lambda self: [n for n, _d in self.entries]  # noqa: E731
    default = lambda self: next((n for n, d in self.entries if d), "")  # noqa: E731
    status_text = lambda self: f"{len(self.entries)} ingress classes"  # noqa: E731


def test_the_ingress_class_box_lists_the_clusters_classes(qtbot):
    classes = ClassList([("alb", False), ("nginx", True)])
    dlg = NetworkEditDialog("Ingress", deepcopy(INGRESS), classes=classes)
    qtbot.addWidget(dlg)
    box = dlg.ingress_class
    assert [box.itemText(i) for i in range(box.count())] == ["", "alb", "nginx"]
    assert "Default class: nginx" in dlg.class_note.text() and box.currentText() == INGRESS["spec"].get("ingressClassName", "")


def test_the_ingress_class_list_arrives_later_and_keeps_what_was_typed(qtbot):
    classes = ClassList([], state="loading")
    dlg = NetworkEditDialog("Ingress", deepcopy(INGRESS), classes=classes)
    qtbot.addWidget(dlg)
    dlg.ingress_class.setCurrentText("custom")
    classes.entries = [("nginx", True)]
    classes.finished.emit()
    assert dlg.ingress_class.currentText() == "custom" and dlg.ingress_class.findText("nginx") >= 0


def test_the_service_lists_ingress_classes_and_marks_the_default():
    flag = "ingressclass.kubernetes.io/is-default-class"
    items = [SimpleNamespace(metadata=SimpleNamespace(name="a", annotations={flag: "true"})),
             SimpleNamespace(metadata=SimpleNamespace(name="b", annotations=None))]
    svc = object.__new__(KubeService)
    svc._apis = {"net": SimpleNamespace(list_ingress_class=lambda **kw: SimpleNamespace(items=items))}
    assert svc.list_ingress_class_names() == [("a", True), ("b", False)]


# ---- pod labels in the selector boxes ------------------------------------------------------------------------
class LabelList:
    def __init__(self, entries, state="done"):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            finished = Signal()
        self._e = Emitter()
        self.finished = self._e.finished
        self.entries, self.state = entries, state

    def choices(self):
        out = {}
        for k, v in self.entries:
            out.setdefault(k, []).append(v)
        return out


def test_the_service_selector_offers_the_pod_labels_of_the_namespace(qtbot):
    labels = LabelList([("app", "api"), ("app", "web"), ("tier", "back")], state="loading")
    dlg = NetworkEditDialog("Service", deepcopy(SERVICE), labels=labels)
    qtbot.addWidget(dlg)
    labels.state = "done"
    labels.finished.emit()
    box = dlg.selector.key_box
    assert [box.itemText(i) for i in range(box.count())] == ["app", "tier"]
    box.setCurrentText("app")
    assert dlg.selector.value_completer.model().stringList() == ["api", "web"]
    assert dlg.selector.value_arrow.isVisible() or not dlg.selector.isVisible()
    box.setCurrentText("unknown")
    assert dlg.selector.value_completer.model().stringList() == []


def test_a_typed_selector_still_works_with_labels_offered(qtbot):
    dlg = NetworkEditDialog("Service", deepcopy(SERVICE), labels=LabelList([("app", "api")]))
    qtbot.addWidget(dlg)
    dlg.selector.key_box.setCurrentText("custom")
    dlg.selector.value_edit.setText("x")
    dlg.selector._add()
    assert dlg.selector.items()["custom"] == "x"


def test_the_service_lists_the_distinct_pod_labels(monkeypatch):
    import json
    pages = [json.dumps({"items": [{"metadata": {"labels": {"app": "a", "t": "1"}}}, {"metadata": {}}],
                         "metadata": {"continue": "c"}}),
             json.dumps({"items": [{"metadata": {"labels": {"app": "a", "t": "2"}}}], "metadata": {}})]
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(list_namespaced_pod=lambda ns, **kw: SimpleNamespace(data=pages.pop(0)))
    assert svc.list_pod_labels("shop") == [("app", "a"), ("t", "1"), ("t", "2")]


# ---- services in the ingress backends -------------------------------------------------------------------------
class NameList:
    def __init__(self, entries, state="done"):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            finished = Signal()
        self._e = Emitter()
        self.finished = self._e.finished
        self.entries, self.state = entries, state


def test_the_ingress_offers_the_namespaces_services_in_rules_and_default_backend(qtbot):
    services = NameList([], state="loading")
    dlg = NetworkEditDialog("Ingress", deepcopy(INGRESS), services=services)
    qtbot.addWidget(dlg)
    dlg.default_service.setCurrentText("typed")
    services.entries, services.state = ["web", "api"], "done"
    services.finished.emit()
    box = dlg.default_service
    assert [box.itemText(i) for i in range(box.count())] == ["", "api", "web"] and box.currentText() == "typed"
    col = [c[0] for c in dlg.rules.columns].index("service")
    assert dlg.rules.table.itemDelegateForColumn(col).choices == ["", "api", "web"]
    assert dlg.rules.table.itemDelegateForColumn(col).editable


def test_the_service_lists_service_names():
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(list_namespaced_service=lambda ns, **kw: SimpleNamespace(
        items=[SimpleNamespace(metadata=SimpleNamespace(name="a")), SimpleNamespace(metadata=SimpleNamespace(name="b"))],
        metadata=SimpleNamespace(_continue=None)))
    assert svc.list_service_names("shop") == ["a", "b"]
