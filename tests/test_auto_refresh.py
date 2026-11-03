"""The foreground list refreshes on a timer and when it comes to the front; background lists do not."""
from types import SimpleNamespace

from kubeman.ui.workspace import ClusterWorkspace


def make_ws(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    loads = []
    ws.load_view = loads.append
    ws.show()
    ws.open_view("pods")
    ws.open_view("nodes")
    loads.clear()
    return ws, loads


def test_nothing_loads_before_connected_or_after_shutdown(qtbot):
    ws, loads = make_ws(qtbot)
    ws._auto_refresh()
    ws.tabs.setCurrentWidget(ws.views["pods"][0])
    assert loads == []
    ws._ready = True
    ws.shutdown()
    ws._auto_refresh()
    assert loads == []


def test_timer_reloads_only_the_foreground_tab(qtbot):
    ws, loads = make_ws(qtbot)
    ws._ready = True
    ws._auto_refresh()
    assert loads == ["nodes"]  # pods is open in the background
    ws._loading.add("nodes")
    ws._auto_refresh()
    assert loads == ["nodes"]  # not while it is still loading


def test_tab_coming_to_front_reloads_and_refresh_skips_background(qtbot):
    ws, loads = make_ws(qtbot)
    ws._ready = True
    ws.tabs.setCurrentWidget(ws.views["pods"][0])
    assert loads == ["pods"]
    loads.clear()
    ws.refresh()
    assert loads == ["namespaces", "pods"]


def test_hidden_workspace_does_not_poll_and_reloads_when_shown(qtbot):
    ws, loads = make_ws(qtbot)
    ws._ready = True
    ws.hide()
    ws._auto_refresh()
    assert loads == []
    ws.show()
    assert loads == ["nodes"]


def test_toggle_off_stops_timer_and_automatic_reloads(qtbot):
    ws, loads = make_ws(qtbot)
    ws._ready = True
    ws._auto_timer.start()
    ws.auto_action.setChecked(False)
    assert not ws._auto_timer.isActive()
    ws._auto_refresh()
    ws.tabs.setCurrentWidget(ws.views["pods"][0])
    ws.hide()
    ws.show()
    assert loads == []  # fully manual while off
    ws.refresh()
    assert loads == ["namespaces", "pods"]  # the Refresh button still works
    ws.auto_action.setChecked(True)
    assert ws._auto_timer.isActive() and loads[-1] == "pods"  # turning it on reloads at once


def test_interval_choice_changes_timer(qtbot):
    ws, _ = make_ws(qtbot)
    assert ws._auto_timer.interval() == 10_000
    ws.auto_interval.setCurrentIndex(3)
    assert ws._auto_timer.interval() == 60_000


def test_namespace_double_click_scopes_toolbar_and_opens_pods(qtbot):
    from kubeman.k8s.models import NamespaceRow

    ws, loads = make_ws(qtbot)
    ws.ns_combo.addItems(["team"])
    ws.ns_table.set_rows([NamespaceRow("team", "Active", "1d", "", {})])
    ws.ns_table._on_double_click(ws.ns_table.proxy.index(0, 0))
    assert ws.ns_combo.currentText() == "team" and ws.foreground_key() == "pods" and loads == ["pods"]


def test_namespace_selector_only_on_namespaced_views(qtbot):
    ws, _ = make_ws(qtbot)
    shown = lambda: all(a.isVisible() for a in ws._ns_actions)  # noqa: E731
    ws.open_view("pods")
    assert shown()
    for key in ("overview", "nodes", "namespaces", "persistentvolumes", "crds"):
        ws.open_view(key)
        assert not shown(), key  # these ignore the namespace, so the selector would be misleading
    for key in ("workloads", "deployments", "services"):
        ws.open_view(key)
        assert shown(), key
    ws.open_view("nodes")
    ws.ns_combo.setCurrentText("All namespaces")
    ws.tabs.setCurrentWidget(ws.views["pods"][0])  # switching tabs by clicking updates it too
    assert shown()
