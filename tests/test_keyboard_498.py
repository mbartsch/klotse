import sys

"""#498: the navigator and the tables can be used from the keyboard; the menus have Alt mnemonics; the cluster-only
shortcuts say why they did nothing."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from klotse.ui.navigator import Navigator
from klotse.ui.overview import POD_COLUMNS
from klotse.ui.resource_table import ResourceTable
from klotse.k8s.models import PodRow


def pod(name):
    return PodRow(name, "shop", "1/1", "Running", 0, "n1", "5m", {"metadata": {"name": name, "namespace": "shop", "uid": "u-" + name}})


def item_for(nav, name):
    stack = [nav.topLevelItem(i) for i in range(nav.topLevelItemCount())]
    while stack:
        node = stack.pop()
        if node.data(0, nav.ROLE_KIND) == "cluster" and node.data(0, nav.ROLE_KEY) == name:
            return node
        stack.extend(node.child(i) for i in range(node.childCount()))
    raise AssertionError(name)


def test_enter_on_a_disconnected_cluster_connects_it(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod"])
    asked = []
    nav.connect_requested.connect(asked.append)
    nav.setCurrentItem(item_for(nav, "prod"))
    nav._on_activate(item_for(nav, "prod"), 0)
    assert asked == ["prod"]


def test_enter_on_a_connected_cluster_brings_it_forward(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod"])
    nav.set_state("prod", "connected")
    brought = []
    nav.context_clicked.connect(brought.append)
    nav._on_activate(item_for(nav, "prod"), 0)
    assert brought == ["prod"]


def test_enter_on_a_pod_row_opens_it_as_a_double_click_does(qtbot):
    table = ResourceTable(POD_COLUMNS, "Pod", customizable=False)
    qtbot.addWidget(table)
    table.set_rows([pod("web-1"), pod("web-2")])
    table.show()
    table.view.setCurrentIndex(table.proxy.index(1, 0))
    table.view.setFocus()
    seen = []
    table.action_requested.connect(lambda action, row: seen.append((action, row.name)))
    QTest.keyClick(table.view, Qt.Key_Return)
    assert seen == [("containers", "web-2")]


def test_escape_clears_the_filter_and_ctrl_f_moves_to_it(qtbot):
    table = ResourceTable(POD_COLUMNS, "Pod", customizable=False)
    qtbot.addWidget(table)
    table.set_rows([pod("web-1")])
    table.show()
    table.filter.setText("web")
    table.filter.setFocus()
    QTest.keyClick(table.filter, Qt.Key_Escape)
    assert table.filter.text() == ""
    table.activateWindow()
    qtbot.waitActive(table)  # keyboard focus follows the active window
    table.view.setFocus()
    qtbot.waitUntil(lambda: table.view.hasFocus(), timeout=5000)
    QTest.keyClick(table.view, Qt.Key_F, Qt.ControlModifier)
    assert table.filter.hasFocus()


def test_the_menus_have_alt_mnemonics_and_exit_and_preferences_have_shortcuts(qtbot, tmp_path):
    from test_palette import window
    win = window(qtbot, tmp_path)
    titles = [a.text() for a in win.menuBar().actions()]
    assert {"&File", "&Edit", "&View", "&Help"} <= set(titles)
    if sys.platform != "darwin":
        assert win.exit_action.shortcut().toString() == "Ctrl+Q"
        assert win.preferences_action.shortcut().toString() == "Ctrl+,"


def test_a_cluster_only_shortcut_says_why_it_did_nothing(qtbot, tmp_path):
    from test_palette import window
    win = window(qtbot, tmp_path)
    message = []
    win.status.messageChanged.connect(message.append)
    assert win.show_search() is False and win.show_multi() is False
    assert any("Open a cluster first" in m for m in message)
