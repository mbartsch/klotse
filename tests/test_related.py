"""Related objects: owners, what an object owns, uses and is used by, shown in Details and clickable."""
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QUrl

from klotse.k8s import related as rel
from klotse.k8s.client import KubeService
from klotse.k8s.models import NodeRow, PodRow
from klotse.k8s.related import Relation
from klotse.ui import related_view
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.workspace import ClusterWorkspace


def obj(name, ns="shop", uid=None, labels=None, owner=None, spec=None, kind=None, **extra):
    meta = {"name": name, "uid": uid or f"uid-{name}", "labels": labels or {}}
    if ns:
        meta["namespace"] = ns
    if owner:
        meta["ownerReferences"] = [{"kind": owner[0], "name": owner[1], "uid": owner[2], "controller": True}]
    return {"metadata": meta, "spec": spec or {}, **extra}


class Fake:
    """A cluster in a dict: kind -> manifests. Selecting is done the way the API server does it."""

    def __init__(self, **objects):
        self.objects, self.asked = objects, []

    def get(self, kind, namespace, name):
        self.asked.append(("get", kind))
        return next((o for o in self.objects.get(kind, []) if o["metadata"]["name"] == name), None)

    def list(self, kind, namespace, label_selector=None, field_selector=None):
        self.asked.append(("list", kind))
        found = [o for o in self.objects.get(kind, []) if not namespace or o["metadata"].get("namespace") == namespace]
        if label_selector:
            pairs = [p.split("=", 1) for p in label_selector.split(",")]
            found = [o for o in found if all(o["metadata"].get("labels", {}).get(k) == v for k, v in pairs)]
        if field_selector:
            node = field_selector.split("=", 1)[1]
            found = [o for o in found if o["spec"].get("nodeName") == node]
        return found


DEP = obj("web", uid="d1", spec={"selector": {"matchLabels": {"app": "web"}}})
RS1 = obj("web-1", uid="r1", labels={"app": "web"}, owner=("Deployment", "web", "d1"), spec={"selector": {"matchLabels": {"app": "web"}}})
RS2 = obj("web-2", uid="r2", labels={"app": "web"}, owner=("Deployment", "web", "d1"), spec={"selector": {"matchLabels": {"app": "web"}}})
OTHER_RS = obj("api-1", uid="r9", labels={"app": "web"}, owner=("Deployment", "api", "d9"))
P1 = obj("web-1-a", labels={"app": "web"}, owner=("ReplicaSet", "web-1", "r1"),
         spec={"nodeName": "n1", "serviceAccountName": "robot", "containers": [{"name": "c", "envFrom": [{"configMapRef": {"name": "cfg"}}]}],
               "volumes": [{"secret": {"secretName": "sec"}}, {"persistentVolumeClaim": {"claimName": "data"}}]})
P2 = obj("web-1-b", labels={"app": "web"}, owner=("ReplicaSet", "web-1", "r1"), spec={"nodeName": "n2"})
P3 = obj("web-2-a", labels={"app": "web"}, owner=("ReplicaSet", "web-2", "r2"), spec={"nodeName": "n1"})
LOOSE = obj("loose", labels={"app": "web"}, spec={"nodeName": "n1"})
SVC = obj("web", spec={"selector": {"app": "web"}})
ING = obj("site", spec={"rules": [{"http": {"paths": [{"backend": {"service": {"name": "web"}}}]}}], "defaultBackend": {"service": {"name": "fallback"}},
                        "tls": [{"secretName": "site-tls"}]})
CLUSTER = Fake(Deployment=[DEP], ReplicaSet=[RS1, RS2, OTHER_RS], Pod=[P1, P2, P3, LOOSE], Service=[SVC], Ingress=[ING],
               EndpointSlice=[obj("web-abc", labels={"kubernetes.io/service-name": "web"})], Job=[], Node=[])


def pairs(relations):
    return [(r.relation, r.kind, r.name, r.depth) for r in relations]


# ---- owners ------------------------------------------------------------------------------------------------------
def test_a_pods_owner_chain_goes_up_to_the_deployment():
    owners = rel.owners(P1, CLUSTER)
    assert pairs(owners) == [("Owned by", "ReplicaSet", "web-1", 0), ("Owned by", "Deployment", "web", 1)]
    assert owners[0].note == "controller" and owners[0].namespace == "shop"


def test_the_chain_stops_when_an_owner_cannot_be_read_or_at_the_depth_limit():
    assert pairs(rel.owners(P1, Fake())) == [("Owned by", "ReplicaSet", "web-1", 0)]  # the owner is gone: no further hop
    assert len(rel.owners(P1, CLUSTER, limit=1)) == 1 and rel.owners(DEP, CLUSTER) == []


# ---- workloads ---------------------------------------------------------------------------------------------------
def test_a_deployment_owns_its_replica_sets_and_their_pods_but_not_others():
    got = pairs(rel.related("Deployment", DEP, CLUSTER))
    assert got == [("Owns", "ReplicaSet", "web-1", 0), ("Owns", "Pod", "web-1-a", 1), ("Owns", "Pod", "web-1-b", 1),
                   ("Owns", "ReplicaSet", "web-2", 0), ("Owns", "Pod", "web-2-a", 1)]


def test_a_replica_set_owns_only_the_pods_that_name_it_as_owner():
    assert [r.name for r in rel.related("ReplicaSet", RS1, CLUSTER) if r.relation == "Owns"] == ["web-1-a", "web-1-b"]


def test_a_cron_job_owns_its_jobs_and_a_job_its_pods():
    cron = obj("nightly", uid="c1")
    job = obj("nightly-1", uid="j1", owner=("CronJob", "nightly", "c1"), spec={"selector": {"matchLabels": {"controller-uid": "j1"}}})
    pod = obj("nightly-1-x", labels={"controller-uid": "j1"}, owner=("Job", "nightly-1", "j1"))
    cluster = Fake(Job=[job], Pod=[pod])
    assert pairs(rel.related("CronJob", cron, cluster)) == [("Owns", "Job", "nightly-1", 0)]
    assert [(r.relation, r.name) for r in rel.related("Job", job, cluster) if r.relation == "Owns"] == [("Owns", "nightly-1-x")]


# ---- a pod -------------------------------------------------------------------------------------------------------
def test_a_pod_shows_its_node_what_it_uses_and_the_services_that_select_it():
    got = pairs(rel.related("Pod", P1, CLUSTER))
    assert ("Runs on", "Node", "n1", 0) in got and ("Uses", "ConfigMap", "cfg", 0) in got and ("Uses", "Secret", "sec", 0) in got
    assert ("Uses", "PersistentVolumeClaim", "data", 0) in got and ("Uses", "ServiceAccount", "robot", 0) in got
    assert ("Selected by", "Service", "web", 0) in got and got[0][0] == "Owned by"  # owners come first
    node = next(r for r in rel.related("Pod", P1, CLUSTER) if r.kind == "Node")
    assert node.namespace == ""  # cluster-scoped


def test_every_place_a_pod_can_name_a_config_map_secret_or_claim_is_read():
    spec = {"serviceAccount": "old-name", "imagePullSecrets": [{"name": "pull"}],
            "volumes": [{"configMap": {"name": "v-cm"}}, {"projected": {"sources": [{"configMap": {"name": "p-cm"}}, {"secret": {"name": "p-sec"}}]}}],
            "containers": [{"env": [{"valueFrom": {"configMapKeyRef": {"name": "e-cm"}}}, {"valueFrom": {"secretKeyRef": {"name": "e-sec"}}},
                                    {"name": "plain", "value": "x"}], "envFrom": [{"secretRef": {"name": "f-sec"}}]}],
            "initContainers": [{"envFrom": [{"configMapRef": {"name": "i-cm"}}]}]}
    refs = rel.pod_references(spec)
    assert refs["ConfigMap"] == {"v-cm", "p-cm", "e-cm", "i-cm"} and refs["Secret"] == {"p-sec", "e-sec", "f-sec", "pull"}
    assert refs["ServiceAccount"] == {"old-name"} and rel.pod_references({})["ServiceAccount"] == {"default"}


# ---- services, ingresses, storage, nodes, and what is used by pods ------------------------------------------------
def test_a_service_selects_pods_is_backed_by_slices_and_used_by_ingresses():
    got = pairs(rel.related("Service", SVC, CLUSTER))
    assert [g for g in got if g[0] == "Selects"] == [("Selects", "Pod", n, 0) for n in ("web-1-a", "web-1-b", "web-2-a", "loose")]
    assert ("Backed by", "EndpointSlice", "web-abc", 0) in got and ("Used by", "Ingress", "site", 0) in got
    assert not [g for g in pairs(rel.related("Service", obj("headless"), Fake(Pod=[P1]))) if g[0] == "Selects"]  # no selector: selects nothing


def test_an_ingress_routes_to_its_services_and_uses_its_tls_secret():
    assert pairs(rel.related("Ingress", ING, CLUSTER)) == [("Routes to", "Service", "fallback", 0), ("Routes to", "Service", "web", 0),
                                                           ("Uses", "Secret", "site-tls", 0)]


def test_a_claim_and_its_volume_point_at_each_other_and_the_claim_lists_its_pods():
    pvc = obj("data", spec={"volumeName": "pv-1", "storageClassName": "gp3"})
    got = pairs(rel.related("PersistentVolumeClaim", pvc, CLUSTER))
    assert ("Bound to", "PersistentVolume", "pv-1", 0) in got and ("Uses", "StorageClass", "gp3", 0) in got and ("Used by", "Pod", "web-1-a", 0) in got
    pv = obj("pv-1", ns="", spec={"claimRef": {"namespace": "shop", "name": "data"}, "storageClassName": "gp3"})
    claim = next(r for r in rel.related("PersistentVolume", pv, CLUSTER) if r.kind == "PersistentVolumeClaim")
    assert (claim.namespace, claim.name) == ("shop", "data")


def test_a_node_lists_its_pods_by_field_selector():
    node = obj("n1", ns="")
    cluster = Fake(Pod=[P1, P2, P3, LOOSE])
    assert [r.name for r in rel.related("Node", node, cluster)] == ["web-1-a", "web-2-a", "loose"]
    assert all(r.relation == "Runs" for r in rel.related("Node", node, cluster))


@pytest.mark.parametrize("kind, name, expected", [("ConfigMap", "cfg", ["web-1-a"]), ("Secret", "sec", ["web-1-a"]),
                                                  ("ServiceAccount", "robot", ["web-1-a"]), ("ServiceAccount", "default", ["web-1-b", "web-2-a", "loose"])])
def test_config_maps_secrets_and_service_accounts_list_the_pods_that_name_them(kind, name, expected):
    got = rel.related(kind, obj(name), CLUSTER)
    assert [r.name for r in got if r.relation == "Used by"] == expected


def test_no_secret_is_ever_fetched_only_pods_are_read_for_it():
    class Strict(Fake):
        def get(self, kind, namespace, name):
            assert kind != "Secret"
            return super().get(kind, namespace, name)

        def list(self, kind, namespace, label_selector=None, field_selector=None):
            assert kind != "Secret"
            return super().list(kind, namespace, label_selector, field_selector)

    rel.related("Secret", obj("sec"), Strict(Pod=[P1]))


def test_long_relations_are_cut_with_a_counted_note(monkeypatch):
    monkeypatch.setattr(rel, "SHOWN", 3)
    pods = [obj(f"p{i}", owner=("ReplicaSet", "rs", "r1")) for i in range(7)]
    got = rel.related("ReplicaSet", obj("rs", uid="r1"), Fake(Pod=pods))
    assert [r.name for r in got] == ["p0", "p1", "p2", "… and 4 more"] and got[-1].note == "more"


def test_a_namespace_with_too_many_pods_says_it_checked_only_some(monkeypatch):
    monkeypatch.setattr(rel, "SCAN_LIMIT", 2)
    pods = [obj(f"p{i}", spec={"serviceAccountName": "x"}) for i in range(5)]
    got = rel.related("ServiceAccount", obj("robot"), Fake(Pod=pods))
    assert got[-1].note == "partial" and "first 2 pods" in got[-1].name


def test_selector_helpers():
    assert rel.selector_text({"b": "2", "a": "1"}) == "a=1,b=2" and rel.selector_text(None) == ""
    assert rel.selects({"app": "web"}, {"app": "web", "x": "y"}) and not rel.selects({}, {"app": "web"}) and not rel.selects({"app": "a"}, None)
    assert rel.ingress_services(ING) == {"web", "fallback"}


# ---- the service builds a fetcher from the API clients ------------------------------------------------------------
class EmptyCore:
    """A core API where every list is empty, so a test only fakes the calls it cares about."""

    def __getattr__(self, name):
        return lambda *a, **kw: SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None))


def service(apps=None, core=None):
    svc = object.__new__(KubeService)
    svc._apis = {"apps": apps or SimpleNamespace(), "core": core or EmptyCore()}
    svc._core = svc._apis["core"]
    return svc


def test_the_service_follows_owners_through_the_apis_and_survives_a_missing_owner():
    apps = SimpleNamespace(read_namespaced_replica_set=lambda name, ns, **kw: RS1, read_namespaced_deployment=lambda name, ns, **kw: DEP)
    assert [r.name for r in service(apps).related_objects("Pod", P2) if r.relation == "Owned by"] == ["web-1", "web"]

    def gone(name, ns, **kw):
        raise ApiException(status=404)
    assert [r.name for r in service(SimpleNamespace(read_namespaced_replica_set=gone)).related_objects("Pod", P2) if r.relation == "Owned by"] == ["web-1"]


def test_the_service_lists_with_selectors_and_caps_what_it_reads(monkeypatch):
    seen = []

    def list_pods(ns, **kw):
        seen.append((ns, kw.get("label_selector")))
        items = [SimpleNamespace(**{}) for _ in range(0)]
        return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None))

    svc = service(apps=SimpleNamespace(read_namespaced_deployment=lambda name, ns, **kw: DEP),
                  core=type("Core", (EmptyCore,), {"list_namespaced_pod": staticmethod(list_pods)})())
    got = svc.related_objects("ReplicaSet", RS1)
    assert [(r.relation, r.name) for r in got] == [("Owned by", "web")]  # the pods are looked up with the selector, and there are none
    assert seen == [("shop", "app=web")]


def test_the_service_never_reads_a_secret_and_an_unreadable_kind_gives_nothing():
    class Core(EmptyCore):
        def read_namespaced_secret(self, *a, **k):
            pytest.fail("a Secret must never be read")

        def list_namespaced_secret(self, *a, **k):
            pytest.fail("a Secret must never be listed")

    svc = service(core=Core())
    assert svc._related_api("Secret") is not None  # the registry knows the kind; the fetcher still refuses it
    assert svc.related_objects("Secret", obj("sec")) == []  # pods are scanned for the name, nothing else
    assert svc._related_api("Widget") is None and svc.related_objects("Widget", obj("x")) == []


# ---- the page ----------------------------------------------------------------------------------------------------
def test_a_link_round_trips_any_name_and_other_links_are_ignored():
    href = related_view.link("Pod", "shop", "a'b\"<c>&d")
    assert related_view.parse_link(href) == ("Pod", "shop", "a'b\"<c>&d") and "'" not in href and "<" not in href
    for bad in ("https://example.com", "klotse-goto:", "klotse-goto:%5B1%2C2%5D", "klotse-goto:not-json", ""):
        assert related_view.parse_link(bad) is None


def test_the_page_groups_relations_in_reading_order_with_links_and_indents():
    page = related_view.render(rel.related("Deployment", DEP, CLUSTER) + rel.owners(P1, CLUSTER), "web")
    assert page.index("Owned by") < page.index("Owns") and "↳" in page  # pods are indented under their replica set
    assert related_view.link("ReplicaSet", "shop", "web-1") in page and "web-1-a" in page


def test_everything_from_the_cluster_is_escaped_in_the_page():
    page = related_view.render([Relation("Uses", "Secret", "<ns>", "<script>alert(1)</script>"), Relation("Used by", "Pod", "", "… and 3 more", 0, "more")])
    assert "<script>" not in page and "&lt;script&gt;" in page and "&lt;ns&gt;" in page and "… and 3 more" in page


def test_nothing_found_says_why():
    assert "No related objects found for web" in related_view.render([], "web")


# ---- the panel ---------------------------------------------------------------------------------------------------
def tagged(name="web", kind="Deployment", raw=None):
    return SimpleNamespace(name=name, namespace="shop", secret_data={}, table_kind=kind, raw=raw or DEP)


def panel(qtbot):
    p = DetailPanel()
    qtbot.addWidget(p)
    p.show()
    return p


def test_the_related_button_is_for_rows_of_a_kind_that_has_it(qtbot):
    p = panel(qtbot)
    p.show_row(tagged())
    assert p.view_bar.isVisible() and p.related_button.isVisible()
    p.show_row(tagged(kind="Container"))
    assert not p.related_button.isVisible()
    p.show_row(SimpleNamespace(name="x", namespace="", secret_data={}, raw={"metadata": {"name": "x"}}))  # a row never tagged
    assert not p.related_button.isVisible()


def test_choosing_related_asks_once_and_shows_the_answer_with_links(qtbot):
    p = panel(qtbot)
    asked = []
    p.related_requested.connect(asked.append)
    r = tagged()
    p.show_row(r)
    p.related_button.click()
    assert asked == [r] and p.related.isVisible() and "Looking for related objects" in p.related.toPlainText()
    p.show_related(r, rel.related("Deployment", DEP, CLUSTER))
    assert "web-1" in p.related.toPlainText() and "Owns" in p.related.toPlainText()
    p.yaml_button.click()
    p.related_button.click()
    assert asked == [r, r]  # asked again: the cluster changes while you watch


def test_late_answers_for_another_row_are_ignored_and_errors_are_shown(qtbot):
    p = panel(qtbot)
    first, second = tagged("a"), tagged("b")
    p.show_row(first)
    p.related_button.click()
    p.show_row(second)
    p.show_related(first, [Relation("Owns", "Pod", "shop", "stale")])
    assert "stale" not in p.related.toPlainText()
    p.show_related(second, error="forbidden")
    assert "Cannot read the related objects: forbidden" in p.related.toPlainText()


def test_clicking_a_link_asks_to_go_there_and_other_links_do_nothing(qtbot):
    p = panel(qtbot)
    went = []
    p.goto_object.connect(lambda *a: went.append(a))
    p._related_clicked(QUrl(related_view.link("ReplicaSet", "shop", "web-1")))
    p._related_clicked(QUrl("https://example.com"))
    assert went == [("ReplicaSet", "shop", "web-1")]


def test_related_is_one_of_the_exclusive_views(qtbot):
    p = panel(qtbot)
    p.show_row(tagged())
    p.related_button.click()
    assert p.related.isVisible() and not p.editor.isVisible() and not p.formatted.isVisible() and not p.events.isVisible()
    p.events_button.click()
    assert p.events.isVisible() and not p.related.isVisible()


# ---- the table and the workspace ---------------------------------------------------------------------------------
def pod_row(name="web-1-a", ns="shop"):
    raw = {**P1, "metadata": {**P1["metadata"], "name": name, "uid": f"uid-{name}"}}  # each row its own identity, like real pods
    return PodRow(name, ns, "1/1", "Running", 0, "n1", "1d", raw)


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.resize(1000, 600)
    return ws


def test_a_selected_row_is_tagged_with_the_kind_of_its_list(qtbot):
    ws = make_ws(qtbot)
    seen = []
    ws.detail.show_row = lambda row: seen.append(getattr(row, "table_kind", None))
    ws.pod_table.selected.disconnect()
    ws.pod_table.selected.connect(ws.detail.show_row)
    ws.pod_table.set_rows([pod_row()])
    ws.pod_table.view.selectRow(0)
    assert seen == ["Pod"]


def test_the_workspace_answers_the_related_view_from_the_service(qtbot):
    calls = []
    relations = [Relation("Owns", "Pod", "shop", "p1")]
    ws = make_ws(qtbot, related_objects=lambda kind, raw: calls.append((kind, raw["metadata"]["name"])) or relations)
    row = tagged()
    ws.detail.show_row(row)
    ws.detail.related_button.click()
    qtbot.waitUntil(lambda: "p1" in ws.detail.related.toPlainText(), timeout=5000)
    assert calls == [("Deployment", "web")]


def test_a_service_without_the_lookup_shows_the_empty_page(qtbot):
    ws = make_ws(qtbot)
    ws.detail.show_row(tagged())
    ws.detail.related_button.click()
    assert "No related objects found" in ws.detail.related.toPlainText()


def test_a_failed_lookup_is_shown_in_the_view_not_in_a_dialog(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def boom(kind, raw):
        raise RuntimeError("forbidden")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: pytest.fail("no dialog for this"))
    ws = make_ws(qtbot, related_objects=boom)
    ws.detail.show_row(tagged())
    ws.detail.related_button.click()
    qtbot.waitUntil(lambda: "Cannot read" in ws.detail.related.toPlainText(), timeout=5000)


def test_going_to_a_pod_opens_its_list_in_its_namespace_with_the_pod_selected(qtbot):
    from klotse.k8s.models import NamespaceRow
    ws = make_ws(qtbot, list_pods=lambda ns=None: [pod_row("a"), pod_row("web-1-a"), pod_row("b")])
    ws._set_namespaces([NamespaceRow("default", "Active", "1d", ""), NamespaceRow("shop", "Active", "1d", "")])
    ws.go_to_object("Pod", "shop", "web-1-a")
    assert ws.namespace() == "shop" and ws.foreground_key() == "pods"
    qtbot.waitUntil(lambda: [r.name for r in ws.pod_table.selected_rows()] == ["web-1-a"], timeout=5000)


def test_all_namespaces_stays_all_namespaces(qtbot):
    ws = make_ws(qtbot, list_pods=lambda ns=None: [pod_row("web-1-a")])
    ws.ns_combo.setCurrentIndex(0)
    ws.go_to_object("Pod", "shop", "web-1-a")
    assert ws.namespace() == "All namespaces"
    qtbot.waitUntil(lambda: [r.name for r in ws.pod_table.selected_rows()] == ["web-1-a"], timeout=5000)


def test_other_kinds_open_their_own_lists_and_nodes_and_namespaces_use_go_to(qtbot):
    ws = make_ws(qtbot, list_resource=lambda key, ns=None: [], list_node_pods=lambda n: [])
    ws.go_to_object("Service", "shop", "web")
    assert ws.foreground_key() == "services"
    went = []
    ws.go_to = lambda what, name: went.append((what, name))
    ws.go_to_object("Node", "", "n1")
    ws.go_to_object("Namespace", "", "shop")
    assert went == [("node", "n1"), ("namespace", "shop")]


def test_a_kind_without_a_list_says_so(qtbot):
    ws = make_ws(qtbot)
    said = []
    ws.message.connect(said.append)
    ws.go_to_object("Widget", "shop", "w")
    assert "no list for Widget" in said[0]


def test_select_when_loaded_waits_for_the_list_and_drops_the_wish_after_it(qtbot):
    ws = make_ws(qtbot)
    table = ws.pod_table
    table.select_when_loaded("b")
    assert table.selected_rows() == []
    table.set_rows([pod_row("a"), pod_row("b")])
    assert [r.name for r in table.selected_rows()] == ["b"]
    table.select_when_loaded("gone")
    table.set_rows([pod_row("a")])
    table.set_rows([pod_row("a"), pod_row("gone")])  # a later load must not suddenly jump
    assert getattr(table, "_goto", None) is None  # the wish is gone, so nothing was jumped to by the later load
    assert "gone" not in [r.name for r in table.selected_rows()]
