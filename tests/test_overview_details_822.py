"""#822: the cluster Overview with nothing selected hides the docked Details panel; another tab or a selection brings it back,
and only a hide Klotse made itself is undone."""
from test_app import FakeService, open_active

from klotse.k8s.models import NodeRow
from klotse.ui.main_window import MainWindow


def workspace(qtbot):
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    win.resize(1200, 700)
    win.show()
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("overview")
    qtbot.waitUntil(lambda: ws.tabs.currentWidget() is ws.overview, timeout=5000)
    return win, ws


def test_the_idle_overview_hides_details_and_another_tab_brings_it_back(qtbot):
    _, ws = workspace(qtbot)
    assert ws.detail_dock.isHidden()
    ws.open_view("pods")
    assert not ws.detail_dock.isHidden()
    ws.open_view("overview")
    assert ws.detail_dock.isHidden()


def test_a_details_panel_the_user_closed_stays_closed(qtbot):
    _, ws = workspace(qtbot)
    ws.open_view("pods")
    ws._close_details()  # the panel's own ×
    ws.open_view("overview")
    ws.open_view("pods")
    assert ws.detail_dock.isHidden()


def test_showing_details_from_panes_on_the_overview_is_kept(qtbot):
    _, ws = workspace(qtbot)
    ws.detail_dock.toggleViewAction().trigger()
    assert not ws.detail_dock.isHidden()
    ws.open_view("pods")
    assert not ws.detail_dock.isHidden()


def test_a_node_picked_on_the_overview_shows_details_and_is_shown_again_on_return(qtbot):
    _, ws = workspace(qtbot)
    ws.overview._set_nodes([NodeRow("node-a", "Ready", "worker", "v1.30", "", 4, 8, "1d",
                                    {"kind": "Node", "metadata": {"name": "node-a", "uid": "uid-node-a"}})])
    ws.overview.nodes_table.selectRow(0)
    assert not ws.detail_dock.isHidden()
    assert ws.detail.identity_header.isVisibleTo(ws.detail)
    ws.open_view("pods")
    ws.open_view("overview")
    assert not ws.detail_dock.isHidden()  # the node is still selected there


def test_a_floating_details_window_is_left_alone(qtbot):
    _, ws = workspace(qtbot)
    ws.open_view("pods")
    ws.detail_dock.setFloating(True)
    ws.open_view("overview")
    assert not ws.detail_dock.isHidden()
