import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QToolButton

from klotse.ui.overview import CLUSTER_CARDS, OverviewView


def test_dashboard_panels_can_be_detached_and_reattached(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    view.resize(1200, 800)
    view.top_panel_splitter.setSizes([300, 700])
    qtbot.wait(20)
    assert view.top_panel_splitter.handle(1).isEnabled()
    assert view.top_panel_splitter.sizes()[0] != view.top_panel_splitter.sizes()[1]
    panel = view.capacity_panel
    original_splitter = view.top_panel_splitter
    button = view.capacity_detach_button

    button.click()
    qtbot.waitUntil(lambda: id(panel) in view._detached_panels)
    dialog = view._detached_panels[id(panel)]["dialog"]
    assert panel.parentWidget() is dialog
    assert isinstance(button, QToolButton)
    assert button.text() == ""
    assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
    assert not button.icon().isNull()
    assert button.toolTip() == "Attach Resource allocation panel"
    assert button.accessibleName() == "Attach Resource allocation panel"

    dialog.close()
    qtbot.waitUntil(lambda: id(panel) not in view._detached_panels)
    assert original_splitter.indexOf(panel) >= 0
    assert button.toolTip() == "Detach Resource allocation panel"
    assert button.accessibleName() == "Detach Resource allocation panel"


@pytest.mark.parametrize("key", ["capacity", "workload_health", "node_health", "recent_events"])
@pytest.mark.parametrize("close_window", [False, True])
def test_reattaching_releases_the_dialog_and_preserves_the_panel(qtbot, key, close_window):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    panel = getattr(view, f"{key}_panel")
    button = getattr(view, f"{key}_detach_button")
    splitter = panel.parentWidget()
    index = splitter.indexOf(panel)

    for _ in range(3):
        button.click()
        dialog = view._detached_panels[id(panel)]["dialog"]
        assert panel.parentWidget() is dialog
        if close_window:
            dialog.close()
        else:
            button.click()
        qtbot.waitUntil(lambda: not view.findChildren(QDialog))
        assert not view._detached_panels
        assert splitter.count() == 2
        assert splitter.indexOf(panel) == index
        assert panel.isVisible()
