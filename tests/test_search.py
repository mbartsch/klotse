"""Search across kinds and namespaces: query parsing, the bounded metadata-only service search, the dialog and its wiring."""
import json
import threading
from types import SimpleNamespace

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from PySide6.QtWidgets import QDialog

from klotse.k8s import search
from klotse.k8s.client import KubeService
from klotse.k8s.resources import SPEC_BY_KIND
from klotse.ui.search_dialog import SearchDialog
from klotse.ui.workspace import ClusterWorkspace


# ---- the query ---------------------------------------------------------------------------------------------------
def test_words_labels_kinds_and_namespaces_are_told_apart():
    q = search.parse_query("API web kind:Deploy kind:svc ns:shop app.kubernetes.io/name=web kind:nonsense")
    assert q.words == ["api", "web"] and q.kinds == ["Deployment", "Service"] and q.namespaces == ["shop"]
    assert q.labels == [("app.kubernetes.io/name", "web")] and q.unknown_kinds == ["nonsense"]
    assert q.label_selector == "app.kubernetes.io/name=web" and not q.empty
    assert search.parse_query("   ").empty and search.parse_query("kind:").words == ["kind:"]


def test_matching_needs_every_word_the_namespace_and_the_exact_label():
    q = search.parse_query("web api ns:shop tier=front")
    ok = {"name": "Web-API-1", "namespace": "shop", "labels": {"tier": "front"}}
    assert search.matches(ok, q)
    assert not search.matches({**ok, "name": "web-1"}, q)
    assert not search.matches({**ok, "namespace": "other"}, q)
    assert not search.matches({**ok, "labels": {"tier": "back"}}, q)
    assert not search.matches({**ok, "labels": None}, q)


def test_a_namespace_term_leaves_out_cluster_wide_kinds_unless_named():
    names = [k[0] for k in search.kinds_for(search.parse_query("x ns:shop"))]
    assert "Node" not in names and "Namespace" not in names and "Pod" in names
    assert [k[0] for k in search.kinds_for(search.parse_query("x kind:node"))] == ["Node"]


def test_every_searched_kind_has_a_list_call_and_somewhere_to_go():
    apis = {"core": client.CoreV1Api, "apps": client.AppsV1Api, "batch": client.BatchV1Api, "net": client.NetworkingV1Api}
    for kind, api, infix, namespaced in search.SEARCH_KINDS:
        cls = apis[api]
        assert hasattr(cls, f"list_{infix}_for_all_namespaces" if namespaced else f"list_{infix}"), kind
        if namespaced:
            assert hasattr(cls, f"list_namespaced_{infix}"), kind
        assert kind in ("Node", "Namespace", "Pod", "Deployment", "ReplicaSet", "StatefulSet") or kind in SPEC_BY_KIND, kind


def test_hit_labels_are_cut():
    h = search.Hit("Pod", "a", "b", labels={f"k{i}": "v" * 20 for i in range(20)})
    assert len(h.label_text(50)) == 50 and h.label_text(50).endswith("…") and search.Hit("Pod", "a", "b").label_text() == ""


# ---- the service -------------------------------------------------------------------------------------------------
class Api:
    """Answers like the client with _preload_content=False: pages of PartialObjectMetadataList. Records every call."""

    def __init__(self, pages, error=None):
        self.pages, self.error, self.calls = pages, error, []

    def __call__(self, *args, **kw):
        self.calls.append((args, kw))
        if self.error:
            raise self.error
        index = int(kw.get("_continue") or 0)
        meta = {"continue": str(index + 1)} if index + 1 < len(self.pages) else {}
        return SimpleNamespace(data=json.dumps({"metadata": meta, "items": self.pages[index]}).encode())


def obj(name, ns="shop", **labels):
    meta = {"name": name, "labels": labels, "creationTimestamp": "2026-01-01T00:00:00Z"}
    if ns:
        meta["namespace"] = ns
    return {"metadata": meta}


def service(**lists):
    """A KubeService whose API objects answer `list_<infix>` style calls from `lists` {api key: {method: Api}}."""
    svc = KubeService.__new__(KubeService)
    svc._apis = {key: SimpleNamespace(**methods) for key, methods in lists.items()}
    for key in ("core", "apps", "batch", "net"):
        svc._apis.setdefault(key, SimpleNamespace())
    return svc


def empty():
    return Api([[]])


def test_a_search_finds_across_kinds_and_asks_for_metadata_only():
    pods = Api([[obj("web-1"), obj("db-1")]])
    deploys = Api([[obj("web")]])
    svc = service(core={"list_pod_for_all_namespaces": pods}, apps={"list_deployment_for_all_namespaces": deploys})
    hits, notes = svc.search_objects("web kind:pod kind:deploy")
    assert [(h.kind, h.name) for h in hits] == [("Pod", "web-1"), ("Deployment", "web")] and notes == []
    assert "PartialObjectMetadataList" in pods.calls[0][1]["_headers"]["Accept"] and pods.calls[0][1]["limit"] == search.PAGE_LIMIT


def test_a_label_term_is_also_a_server_side_selector_and_one_namespace_uses_the_namespaced_call():
    pods = Api([[obj("a", tier="front"), obj("b", tier="back")]])
    svc = service(core={"list_namespaced_pod": pods})
    hits, _ = svc.search_objects("tier=front kind:pod", namespace="shop")
    assert [h.name for h in hits] == ["a"]
    args, kw = pods.calls[0]
    assert args == ("shop",) and kw["label_selector"] == "tier=front"


def test_pages_are_followed_and_the_hit_limit_stops_the_search():
    pods = Api([[obj(f"p{i}") for i in range(3)], [obj(f"p{i}") for i in range(3, 6)]])
    svc = service(core={"list_pod_for_all_namespaces": pods})
    hits, notes = svc.search_objects("p kind:pod")
    assert len(hits) == 6 and notes == [] and len(pods.calls) == 2
    hits, notes = svc.search_objects("p kind:pod", limit=4)
    assert len(hits) == 4 and "first 4 matches" in notes[0]


def test_a_huge_kind_is_cut_at_the_page_cap_with_a_note(monkeypatch):
    monkeypatch.setattr(search, "MAX_PAGES", 3)
    pods = Api([[obj("x")]] * 10)
    hits, notes = service(core={"list_pod_for_all_namespaces": pods}).search_objects("zzz kind:pod")
    assert hits == [] and len(pods.calls) == 3 and "only the first" in notes[0]


def test_a_kind_without_permission_is_noted_and_the_others_still_answer():
    forbidden = Api([], error=ApiException(status=403))
    svc = service(core={"list_secret_for_all_namespaces": forbidden, "list_pod_for_all_namespaces": Api([[obj("web")]])})
    hits, notes = svc.search_objects("web kind:secret kind:pod")
    assert [h.name for h in hits] == ["web"] and notes == ["Secret: not searched (no permission)."]


def test_a_cancelled_search_stops_between_kinds_and_pages():
    cancel = threading.Event()
    cancel.set()
    pods = Api([[obj("web")]])
    assert service(core={"list_pod_for_all_namespaces": pods}).search_objects("web", cancel=cancel) == ([], [])
    assert pods.calls == []


def test_an_empty_query_reads_nothing_and_an_unknown_kind_is_reported():
    svc = service()
    assert svc.search_objects("  ") == ([], [])
    assert svc.search_objects("kind:bogus") == ([], ["'bogus' is not a kind that is searched."])


def test_cluster_wide_kinds_use_the_unnamespaced_list():
    nodes = Api([[obj("node-1", ns=None)]])
    hits, _ = service(core={"list_node": nodes}).search_objects("node kind:node")
    assert [(h.kind, h.namespace) for h in hits] == [("Node", "")] and nodes.calls[0][0] == ()


# ---- the dialog and the workspace --------------------------------------------------------------------------------
def test_the_dialog_asks_ignores_stale_answers_and_shows_hits(qtbot):
    d = SearchDialog("ctx", "shop")
    qtbot.addWidget(d)
    asked = []
    d.search_requested.connect(lambda text, ns, cancel: asked.append((text, ns, cancel)))
    d.box.setText("web")
    d.search_now()
    first = asked[0][2]
    assert asked[0][:2] == ("web", "") and d.status.text() == "Searching…"
    d.only_ns.setChecked(True)  # toggling searches again with the namespace, and stops the first search
    assert first.is_set() and asked[1][:2] == ("web", "shop")
    hit = search.Hit("Pod", "shop", "web-1", "2026-01-01T00:00:00Z", {"a": "b"})
    d.show_results(1, [hit], [])  # an answer to the first search: ignored
    assert d.table.rowCount() == 0
    d.show_results(d.serial, [hit], ["Secret: not searched (no permission)."])
    assert d.table.rowCount() == 1 and d.table.item(0, 2).text() == "web-1" and d.status.text().startswith("1 match.")
    went = []
    d.goto_requested.connect(lambda *a: went.append(a))
    d.table.itemDoubleClicked.emit(d.table.item(0, 0))
    assert went == [("Pod", "shop", "web-1")]


def test_no_matches_and_failures_are_said(qtbot):
    d = SearchDialog("ctx", "")
    qtbot.addWidget(d)
    assert d.only_ns.isHidden()
    d.box.setText("zzz")
    d.search_now()
    d.show_results(d.serial, [], [])
    assert d.status.text() == "0 matches." and not d.go_button.isEnabled()
    d.search_now()
    d.show_error(d.serial, "boom")
    assert d.status.text() == "Search failed: boom"
    d.box.setText("")
    d.search_now()
    assert d.status.text() == "Type a query."


def make_ws(qtbot, answer):
    calls = []

    def search_objects(text, namespace, cancel):
        calls.append((text, namespace))
        return answer
    ws = ClusterWorkspace(SimpleNamespace(search_objects=search_objects), "ctx")
    qtbot.addWidget(ws)
    return ws, calls


def test_the_workspace_runs_the_search_on_a_worker_and_goes_to_the_hit(qtbot):
    hit = search.Hit("Service", "shop", "api")
    ws, calls = make_ws(qtbot, ([hit], []))
    d = ws.show_search()
    assert ws.show_search() is d  # one window per workspace
    d.box.setText("api")
    d.search_now()
    qtbot.waitUntil(lambda: d.table.rowCount() == 1, timeout=5000)
    assert calls == [("api", None)]
    went = []
    ws.go_to_object = lambda *a: went.append(a)
    d.goto_requested.emit("Service", "shop", "api")
    assert went == [("Service", "shop", "api")]
    ws.shutdown()
    assert not d.isVisible()


def test_a_service_that_cannot_search_says_so(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    d = ws.show_search()
    d.box.setText("x")
    d.search_now()
    assert "cannot search" in d.status.text()
    ws.shutdown()


def test_the_view_menu_entry_opens_the_search_for_the_cluster_in_front(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService, open_active
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    assert win.show_search() is False  # no cluster open yet
    open_active(win)
    ws = win.workspace("ctx")
    assert win.show_search() is True and ws._search_dialog.isVisible()
    assert win.search_action.shortcut().toString() == "Ctrl+Shift+F"
    ws._search_dialog.close()
