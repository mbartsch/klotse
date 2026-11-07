from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s.client import BIG_PAGE_SIZE, PAGE_SIZE, KubeService
from klotse.ui import big_clusters
from klotse.ui.big_clusters import BigClusters
from klotse.ui.workspace import ClusterWorkspace


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)


def test_thresholds():
    assert big_clusters.looks_big(50, 10) and big_clusters.looks_big(3, 1500)
    assert not big_clusters.looks_big(49, 1499)


def test_detected_marks_once_and_the_users_choice_wins(store):
    clusters = BigClusters(store)
    assert clusters.mode("a") == "" and not clusters.is_big("a")
    assert clusters.detected("a") is True and clusters.mode("a") == "auto" and clusters.is_big("a")
    assert clusters.detected("a") is False
    clusters.set_mode("a", "off")
    assert not clusters.is_big("a") and clusters.detected("a") is False  # chosen off: never switched on again by itself
    clusters.set_mode("b", "on")
    assert BigClusters(store).is_big("b") and not BigClusters(store).is_big("a")
    clusters.set_mode("b", "")
    assert BigClusters(store).mode("b") == ""


def test_page_size_follows_the_mode():
    service = KubeService()
    sizes = []

    def fetch(limit, _continue, _request_timeout):
        sizes.append(limit)
        return SimpleNamespace(items=[1], metadata=SimpleNamespace(_continue=None))

    list(service._iter_list(fetch))
    service.set_big(True)
    list(service._iter_list(fetch))
    service.set_big(False)
    list(KubeService._iter_list(fetch))
    assert sizes == [PAGE_SIZE, BIG_PAGE_SIZE, PAGE_SIZE]


def events_service(big):
    service = KubeService()
    service.set_big(big)
    seen = {}
    events = []  # only the request matters here

    def items(key, namespace=None, **options):
        seen.update(options)
        return iter(events)

    service._resource_items = items
    return service, seen


def test_overview_events_ask_for_warnings_only_in_big_mode():
    small, seen_small = events_service(False)
    small._overview_events()
    big, seen_big = events_service(True)
    big._overview_events()
    assert seen_small == {} and seen_big == {"field_selector": "type=Warning"}


def test_health_data_is_the_same_in_both_modes():
    answers = []
    for big in (False, True):
        service = KubeService()
        service.set_big(big)
        rows = lambda raw: [SimpleNamespace(raw=raw)]
        service.list_pods = lambda ns: rows({"p": 1})
        service.list_nodes = lambda: (_ for _ in ()).throw(PermissionError("no"))
        service.list_deployments = lambda ns: rows({"d": 1})
        service.list_statefulsets = lambda ns: rows({"s": 1})
        service.list_resource = lambda key, ns: rows({key: 1})
        answers.append(service.health_data("shop"))
    assert answers[0] == answers[1] and answers[0][1] == ["nodes (PermissionError)"]


def test_capacity_data_reads_both_lists():
    for big in (False, True):
        service = KubeService()
        service.set_big(big)
        service.list_nodes = lambda: ["n"]
        service.list_pods = lambda ns: ["p"]
        assert service.capacity_data() == (["n"], ["p"])


def make_workspace(qtbot):
    service = SimpleNamespace()
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    return ws, service


def test_workspace_reports_a_big_cluster_once_from_the_overview(qtbot):
    ws, _service = make_workspace(qtbot)
    found = []
    ws.big_detected.connect(lambda nodes, pods: found.append((nodes, pods)))
    ws._overview_loaded({"counts": {"nodes": 3, "pods": 17}, "events": [], "lists": {}})
    assert found == []
    ws._overview_loaded({"counts": {"nodes": 103, "pods": 2683}, "events": [], "lists": {}})
    assert found == [(103, 2683)]
    ws.set_big(True)
    ws._overview_loaded({"counts": {"nodes": 103, "pods": 2683}, "events": [], "lists": {}})
    assert found == [(103, 2683)]  # already big: not reported again


def test_set_big_tells_the_service_and_slows_the_heavy_lists(qtbot):
    ws, service = make_workspace(qtbot)
    seen = []
    service.set_big = seen.append
    changes = []
    ws.big_changed.connect(changes.append)
    ws.set_big(True)
    assert seen == [True] and changes == [True] and ws.big
    gap = ws._auto_timer.interval() / 1000
    assert ws._refresh_gap("overview") == max(gap, big_clusters.MIN_REFRESH_S) and ws._refresh_gap("services") == gap
    ws.set_big(False)
    assert ws._refresh_gap("overview") == gap


def test_a_big_cluster_overview_is_not_reloaded_within_the_gap(qtbot):
    ws, _service = make_workspace(qtbot)
    ws._ready = True
    loads = []
    ws.loaders["overview"] = (lambda *a: None, False, lambda *a: None)
    ws.load_view = loads.append
    ws.foreground_key = lambda: "overview"
    ws._clock = lambda: 100.0
    ws._last_load["overview"] = 90.0  # 10 s ago
    ws.load_foreground()
    assert loads == ["overview"]  # small cluster: reloads at the usual interval
    loads.clear()
    ws.set_big(True)
    ws.load_foreground()
    assert loads == []  # big cluster: waits for 30 s
    ws._last_load["overview"] = 60.0
    ws.load_foreground()
    assert loads == ["overview"]


def window(qtbot, tmp_path):
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def test_main_window_marks_the_tab_and_remembers_a_found_big_cluster(qtbot, tmp_path):
    from test_app import open_active
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    assert not ws.big and win.clusters.tabText(0) == ws.context
    ws.big_detected.emit(103, 2683)
    assert ws.big and win.clusters.tabText(0) == f"{ws.context} (big)"
    assert "big-cluster mode is on" in win.status.currentMessage()
    assert BigClusters(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)).mode(ws.context) == "auto"
    ws.set_read_only(True)
    assert win.clusters.tabText(0) == f"{ws.context} 🔒 (big)"


def test_the_view_menu_choice_wins_and_a_second_start_is_already_big(qtbot, tmp_path):
    from test_app import open_active
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    win._sync_big_action()
    assert win.big_action.isEnabled() and not win.big_action.isChecked()
    ws.refresh = lambda: None
    win.set_big_mode(True)
    assert ws.big and BigClusters(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)).mode(ws.context) == "on"
    win.set_big_mode(False)  # the user says not big: automatic detection must not turn it on again
    assert not ws.big
    ws.big_detected.emit(103, 2683)
    assert not ws.big
    second = window(qtbot, tmp_path)
    open_active(second)
    assert not second.clusters.currentWidget().big


# ---- huge clusters: the Overview counts by phase -----------------------------------------------------------------
def pod_object(name, phase="Running"):
    from kubernetes import client
    return client.V1Pod(metadata=client.V1ObjectMeta(name=name, namespace="shop"), spec=client.V1PodSpec(containers=[client.V1Container(name="c")]),
                        status=client.V1PodStatus(phase=phase))


class FakePods:
    """list_pod_for_all_namespaces with limit and field_selector, like the API server: remainingItemCount only for the unfiltered
    limit=1 call."""

    def __init__(self, total, pending=3, failed=2, succeeded=4):
        self.total, self.by_phase = total, {"Pending": pending, "Failed": failed, "Succeeded": succeeded}
        self.calls = []

    def __call__(self, limit=500, _continue=None, _request_timeout=None, field_selector=None):
        self.calls.append((limit, field_selector))
        if field_selector:
            phase = field_selector.split("=")[1]
            items = [pod_object(f"{phase}-{i}", phase) for i in range(self.by_phase[phase])]
            return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None, remaining_item_count=None))
        return SimpleNamespace(items=[pod_object("first")][:limit], metadata=SimpleNamespace(_continue="x", remaining_item_count=self.total - 1))


def huge_service(total, big=True, **phases):
    service = KubeService()
    service.set_big(big)
    service._core = SimpleNamespace(list_pod_for_all_namespaces=FakePods(total, **phases))
    return service


def test_a_huge_cluster_is_counted_by_phase_without_reading_every_pod():
    service = huge_service(18000)
    counts, lists = service._overview_pods()
    assert counts["pods"] == 18000 and counts["pending"] == 3 and counts["failed"] == 2 and counts["by_phase"]
    assert counts["running"] == 18000 - 3 - 2 - 4
    assert len(lists["pending"]) == 3 and len(lists["failed"]) == 2
    calls = service._core.list_pod_for_all_namespaces.calls
    assert (1, None) in calls and not any(limit == 500 and not sel for limit, sel in calls)  # never a full listing


def test_below_the_threshold_or_outside_big_mode_the_full_pass_is_used():
    small = huge_service(4000)
    assert small._overview_pods_by_phase() is None
    off = huge_service(18000, big=False)
    called = []
    off._core.list_pod_for_all_namespaces = lambda **kw: called.append(kw) or SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None))
    counts, _ = off._overview_pods()
    assert counts["pods"] == 0 and "by_phase" not in counts and called[0]["limit"] == 500  # the plain full listing


def test_a_server_without_a_count_falls_back():
    service = huge_service(18000)
    service._core.list_pod_for_all_namespaces = lambda **kw: SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None, remaining_item_count=None))
    assert service._overview_pods_by_phase() is None


def test_the_workspace_says_when_the_overview_counts_by_phase(qtbot):
    ws, _service = make_workspace(qtbot)
    said = []
    ws.message.connect(said.append)
    ws._overview_loaded({"counts": {"nodes": 632, "pods": 18000, "by_phase": True}, "events": [], "lists": {}})
    assert any("by phase" in text and "18,000" in text for text in said)


# ---- the paged Pods list of a huge cluster ------------------------------------------------------------------------
class PagedPods:
    """list_pod_for_all_namespaces with limit/continue: `total` pods named p0..; remainingItemCount on every unfiltered page."""

    def __init__(self, total):
        self.total, self.calls = total, []

    def __call__(self, limit=500, _continue=None, _request_timeout=None, field_selector=None):
        start = int(_continue or 0)
        self.calls.append((limit, _continue))
        stop = min(start + limit, self.total)
        items = [pod_object(f"p{i}") for i in range(start, stop)]
        return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=str(stop) if stop < self.total else None,
                                                                     remaining_item_count=self.total - stop))


def pager_service(total):
    service = KubeService()
    service.set_big(True)
    service._core = SimpleNamespace(list_pod_for_all_namespaces=PagedPods(total))
    service.pod_usage = lambda namespace=None: {}
    return service


def test_the_pager_reads_one_page_at_a_time_and_knows_the_total():
    service = pager_service(5)
    pager = service.pod_pager()
    pager.limit = 2
    first = pager.next()
    assert [r.name for r in first] == ["p0", "p1"] and pager.total == 5 and not pager.done and pager.loaded == 2
    assert [r.name for r in pager.next()] == ["p2", "p3"]
    assert [r.name for r in pager.next()] == ["p4"] and pager.done and pager.loaded == 5
    assert pager.next() == []
    assert [limit for limit, _ in service._core.list_pod_for_all_namespaces.calls] == [2, 2, 2]


def test_the_pager_asks_for_metrics_once():
    service = pager_service(4)
    asked = []
    service.pod_usage = lambda namespace=None: asked.append(1) or {}
    pager = service.pod_pager()
    pager.limit = 2
    pager.next()
    pager.next()
    assert asked == [1]


def huge_workspace(qtbot, total):
    ws, service = make_workspace(qtbot)
    service.pod_pager = lambda namespace=None: _pager(total, namespace)
    service.list_pods = lambda namespace=None: []
    ws.set_big(True)
    ws.huge = True
    return ws, service


def _pager(total, namespace):
    pager = pager_service(total).pod_pager(namespace)
    pager.limit = 2
    return pager


def test_a_huge_cluster_loads_a_first_page_and_load_more_adds_the_next(qtbot):
    ws, _service = huge_workspace(qtbot, 5)
    rows = ws._read_pods(None)
    ws._pods_loaded(rows)
    table = ws.pod_table
    assert table.model.rowCount() == 2 and table.more_button.isVisibleTo(table) and "first 2 of 5 pods" in table.status.text()
    ws.load_more_pods()
    qtbot.waitUntil(lambda: table.model.rowCount() == 4, timeout=5000)
    ws.load_more_pods()
    qtbot.waitUntil(lambda: table.model.rowCount() == 5, timeout=5000)
    assert not table.more_button.isVisibleTo(table) and "all 5 pods loaded" in table.status.text()


def test_the_huge_pods_list_reloads_only_on_request_and_a_small_cluster_is_unchanged(qtbot):
    ws, _service = huge_workspace(qtbot, 5)
    assert ws.is_manual("pods")
    ws.huge = False
    assert not ws.is_manual("pods")
    ws.huge = True
    ws.set_big(False)
    assert not ws.is_manual("pods")


def test_a_reload_starts_over_and_an_old_pages_answer_is_dropped(qtbot):
    ws, _service = huge_workspace(qtbot, 6)
    ws._pods_loaded(ws._read_pods(None))
    old = ws._pod_pager
    ws.load_more_pods()
    ws._pods_loaded(ws._read_pods(None))  # a refresh replaced the pager while the page was coming
    assert ws._pod_pager is not old
    qtbot.wait(300)
    assert ws.pod_table.model.rowCount() == 2


# ---- node pod counts from the server-side Table -------------------------------------------------------------------
import json


class FakeRest:
    """api.rest_client.request returning Table pages like the API server (with a continue token between pages)."""

    def __init__(self, pages, fail=False):
        self.pages, self.urls, self.headers, self.fail = list(pages), [], [], fail

    def request(self, method, url, headers=None, _request_timeout=None):
        self.urls.append(url)
        self.headers.append(headers)
        if self.fail:
            raise OSError("proxy stripped the Accept header")
        page = self.pages.pop(0)
        body = {"kind": "Table", "columnDefinitions": [{"name": "Name"}, {"name": "Node"}],
                "rows": [{"cells": [f"p{i}", node]} for i, node in enumerate(page["nodes"])], "metadata": {"continue": page.get("next")}}
        return SimpleNamespace(data=json.dumps(body).encode(), status=200)


def table_service(pages, fail=False):
    service = KubeService()
    service.set_big(True)
    rest = FakeRest(pages, fail)
    cfg = SimpleNamespace(host="https://api.example", auth_settings=lambda: {"BearerToken": {"in": "header", "key": "authorization", "value": "Bearer t"}})
    service._api_client = SimpleNamespace(configuration=cfg, default_headers={"Accept-Encoding": "gzip"}, rest_client=rest)
    return service, rest


def test_node_pod_counts_come_from_the_table_pages():
    service, rest = table_service([{"nodes": ["n1", "n1", "<none>", "n2"], "next": "tok"}, {"nodes": ["n2", None, "n1"]}])
    assert service.all_node_pod_counts() == {"n1": 3, "n2": 2}
    assert "continue=tok" in rest.urls[1] and "fieldSelector=status.phase%21%3DSucceeded" in rest.urls[0]
    assert rest.headers[0]["Accept"].startswith("application/json;as=Table") and rest.headers[0]["authorization"] == "Bearer t"


def test_when_the_table_cannot_be_read_the_plain_pass_is_used():
    service, _rest = table_service([], fail=True)
    plain = SimpleNamespace(items=[SimpleNamespace(spec=SimpleNamespace(node_name="n1"))], metadata=SimpleNamespace(_continue=None))
    service._core = SimpleNamespace(list_pod_for_all_namespaces=lambda **kw: plain)
    assert service.all_node_pod_counts() == {"n1": 1}


def test_small_clusters_never_use_the_table():
    service, rest = table_service([])
    service.set_big(False)
    plain = SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None))
    service._core = SimpleNamespace(list_pod_for_all_namespaces=lambda **kw: plain)
    assert service.all_node_pod_counts() == {} and rest.urls == []


def test_big_mode_recounts_node_pods_less_often(qtbot):
    from klotse.ui.workspace import NODE_PODS_BIG_REFRESH_MS, NODE_PODS_REFRESH_MS
    ws, _service = make_workspace(qtbot)
    ws.set_big(True)
    assert ws._node_pods_timer.interval() == NODE_PODS_BIG_REFRESH_MS
    ws.set_big(False)
    assert ws._node_pods_timer.interval() == NODE_PODS_REFRESH_MS


def test_a_failed_table_read_is_written_to_the_timing_log(tmp_path, monkeypatch):
    from klotse.k8s import timing
    monkeypatch.delenv(timing.ENV, raising=False)
    timing.timings.configure(tmp_path / "t.log", True)
    try:
        service, _rest = table_service([], fail=True)
        service._core = SimpleNamespace(list_pod_for_all_namespaces=lambda **kw: SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None)))
        service.all_node_pod_counts()
        entry = [json.loads(line) for line in open(tmp_path / "t.log", encoding="utf-8") if '"fallback"' in line][0]
        assert entry["error"] == "OSError" and "Accept header" in entry["detail"]
    finally:
        timing.timings.configure(None, False)
