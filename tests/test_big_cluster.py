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
