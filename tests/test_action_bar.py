"""One Actions menu instead of a row of buttons, labels that shorten instead of being covered, and the Details size cap."""
from types import SimpleNamespace

from PySide6.QtCore import Qt

from klotse.ui.action_bar import ElidedLabel, actions_button
from klotse.ui.node_bar import NodeActionBar
from klotse.ui.pod_view import PodActionBar
from klotse.ui.workspace import ClusterWorkspace


def test_the_pod_bar_has_one_actions_button_with_a_menu(qtbot):
    bar = PodActionBar()
    qtbot.addWidget(bar)
    assert bar.menu_button.text() == "Actions"
    labels = [a.text() for a in bar.menu_button.menu().actions() if not a.isSeparator()]
    assert labels == ["Logs", "Edit…", "Terminate…", "Force terminate…"]
    assert sum(1 for a in bar.menu_button.menu().actions() if a.isSeparator()) == 1  # the destructive ones are set apart


def test_the_node_bar_has_one_actions_button_and_cordon_toggles(qtbot):
    bar = NodeActionBar("n1")
    qtbot.addWidget(bar)
    assert bar.menu_button.text() == "Actions"
    assert [a.text() for a in bar.items] == ["Cordon", "Drain…", "Shell (nsenter)…", "Delete node…", "Force delete…"]


def test_actions_button_emits_the_action_name(qtbot):
    got = []
    button, actions = actions_button([("One", "one"), None, ("Two", "two")], got.append)
    qtbot.addWidget(button)
    actions[1].trigger()
    actions[0].trigger()
    assert got == ["two", "one"]


def test_a_gone_pod_disables_the_menu(qtbot):
    bar = PodActionBar()
    qtbot.addWidget(bar)
    bar.set_gone("web")
    assert not bar.menu_button.isEnabled() and "no longer" in bar.state.text()


def test_a_long_label_is_cut_not_covered_and_keeps_its_text(qtbot):
    bar = PodActionBar()
    qtbot.addWidget(bar)
    bar.resize(260, 30)
    bar.set_row(SimpleNamespace(namespace="a-very-long-namespace-name", name="a-very-long-pod-name-1234567890", status="Running",
                                node="ip-10-0-0-1.eu-west-1.compute.internal"))
    bar.show()
    assert bar.title.text().startswith("Pod a-very-long") and "compute.internal" in bar.state.text()  # full text kept
    assert bar.title.toolTip() == bar.title.text()
    assert bar.title.minimumSizeHint().width() <= 40  # it never asks for the width of its text
    assert bar.sizeHint().width() < 600 or bar.minimumSizeHint().width() < 260


def test_the_label_class_reports_full_text(qtbot):
    label = ElidedLabel("hello world")
    qtbot.addWidget(label)
    assert label.text() == "hello world" and label.toolTip() == "hello world"


def test_the_details_dock_is_capped_at_half_the_window(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.resize(1200, 800)
    ws.show()
    qtbot.waitUntil(lambda: ws.detail_dock.maximumWidth() == 600, timeout=2000)
    assert ws.detail_dock.maximumHeight() > 10000  # only the width is capped at the side
    ws.resize(800, 800)
    qtbot.waitUntil(lambda: ws.detail_dock.maximumWidth() == 400, timeout=2000)
    ws.addDockWidget(Qt.BottomDockWidgetArea, ws.detail_dock)
    qtbot.waitUntil(lambda: ws.detail_dock.maximumHeight() == 400, timeout=2000)
    assert ws.detail_dock.maximumWidth() > 10000
