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
    assert button.text() == "Attach"

    dialog.close()
    qtbot.waitUntil(lambda: id(panel) not in view._detached_panels)
    assert original_splitter.indexOf(panel) >= 0
    assert button.text() == "Detach"
