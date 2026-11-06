"""The foreground list refreshes on a timer and when it comes to the front; background lists do not."""
from types import SimpleNamespace

from klotse.ui.workspace import ClusterWorkspace


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
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(60_000))
    assert ws._auto_timer.interval() == 60_000


def test_namespace_double_click_scopes_toolbar_and_opens_pods(qtbot):
    from klotse.k8s.models import NamespaceRow

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
    ws.set_namespace("All namespaces")
    ws.tabs.setCurrentWidget(ws.views["pods"][0])  # switching tabs by clicking updates it too
    assert shown()


# ---- one control: Auto-refresh: Off / 5 s / 10 s / 30 s / 60 s --------------------------------------------------------
def test_the_toolbar_has_one_auto_refresh_control_with_an_off_choice(qtbot):
    ws, _ = make_ws(qtbot)
    assert [ws.auto_interval.itemText(i) for i in range(ws.auto_interval.count())] == ["Off", "5 s", "10 s", "30 s", "60 s"]
    assert ws.auto_interval.currentText() == "10 s" and ws.auto_action.isChecked()
    from PySide6.QtWidgets import QLabel, QToolBar
    toolbar = ws.findChildren(QToolBar)[0]
    assert ws.auto_action not in toolbar.actions()  # no second, separate on/off button next to the combo
    assert any(label.text() == " Auto-refresh: " for label in toolbar.findChildren(QLabel))


def test_choosing_off_stops_the_timer_and_choosing_an_interval_starts_it(qtbot):
    ws, _ = make_ws(qtbot)
    ws._ready = True
    ws._auto_timer.start()
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(0))
    assert not ws.auto_action.isChecked() and not ws._auto_timer.isActive() and ws._watchers == []
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(5_000))
    assert ws.auto_action.isChecked() and ws._auto_timer.isActive() and ws._auto_timer.interval() == 5_000


def test_the_combo_follows_the_on_off_state_when_code_changes_it(qtbot):
    ws, _ = make_ws(qtbot)
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(30_000))
    ws.auto_action.setChecked(False)
    assert ws.auto_interval.currentText() == "Off"
    ws.auto_action.setChecked(True)
    assert ws.auto_interval.currentText() == "30 s" and ws._auto_timer.interval() == 30_000  # back to what it was


def test_off_then_on_by_the_combo_returns_to_the_chosen_interval_not_a_default(qtbot):
    ws, _ = make_ws(qtbot)
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(60_000))
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(0))
    ws.auto_interval.setCurrentIndex(ws.auto_interval.findData(10_000))
    assert ws._auto_timer.interval() == 10_000 and ws.auto_action.isChecked()


def test_right_clicking_the_toolbar_cannot_remove_it_but_the_panes_can_still_be_brought_back(qtbot):
    ws, _ = make_ws(qtbot)
    menu = ws.createPopupMenu()
    qtbot.addWidget(menu)
    assert [a.text() for a in menu.actions()] == [ws.detail_dock.windowTitle(), ws.log_dock.windowTitle()]  # docks only, no toolbar entry
    ws.show()
    ws.detail_dock.close()
    assert not ws.detail_dock.isVisible()
    ws.detail_dock.toggleViewAction().trigger()
    assert ws.detail_dock.isVisible()
