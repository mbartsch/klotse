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
