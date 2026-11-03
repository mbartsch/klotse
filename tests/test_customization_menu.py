"""The Customizations menu offers only the label columns that apply to the list in front."""
from kubeman.ui.main_window import MainWindow

from test_app import FakeService


def visible(win):
    win._update_customizations()
    return sorted(k for k, a in win.customization_actions.items() if a.isVisible())


def test_menu_follows_the_foreground_list(qtbot):
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws._ready, timeout=5000)

    assert ws.foreground_kind() is None  # overview
    assert visible(win) == [] and win.no_customizations.isVisible()

    ws.open_view("pods")
    assert ws.foreground_kind() == "Pod" and visible(win) == ["Pod"] and not win.no_customizations.isVisible()

    ws.open_view("nodes")
    assert ws.foreground_kind() == "Node" and visible(win) == ["Node"]

    ws.open_view("deployments")  # neither kind applies
    assert visible(win) == [] and win.no_customizations.isVisible()

    ws.open_node_pods("n1")  # pods on a node are pods too
    assert visible(win) == ["Pod"]


def test_switching_view_updates_menu_without_opening_it(qtbot):
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws._ready, timeout=5000)
    ws.open_view("nodes")
    assert win.customization_actions["Node"].isVisible()
    ws.open_view("pods")
    assert win.customization_actions["Pod"].isVisible()
    assert not win.customization_actions["Node"].isVisible()
