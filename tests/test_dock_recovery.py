from types import SimpleNamespace

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ClusterWorkspace


def make_workspace(qtbot):
    workspace = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(workspace)
    workspace.resize(900, 600)
    workspace.show()
    return workspace


def test_selection_and_tab_changes_clear_stale_details(qtbot):
    workspace = make_workspace(qtbot)
    assert workspace.tabs.accessibleName() == "Resource views for ctx"
    assert workspace.log_tabs.accessibleName() == "Logs and activity for ctx"
    assert workspace.ns_combo.accessibleName() == "Filter resource lists by namespace"
    assert workspace.pin_button.accessibleName().startswith("Pin namespace ")
    assert workspace.panes_button.accessibleName() == "Show or hide workspace panels"
    row = PodRow("api-1", "shop", "1/1", "Running", 0, "node-a", "1m",
                 {"metadata": {"name": "api-1", "namespace": "shop", "uid": "uid-1"}})
    workspace.pod_table.set_rows([row])
    workspace.pod_table.view.selectRow(0)
    assert workspace.detail.identity_title.text() == "Pod · api-1"

    workspace.pod_table.view.clearSelection()
    assert not workspace.detail.identity_header.isVisible()
    assert workspace.detail.editor.placeholderText() == "Select a resource to see its details."
    assert not workspace.detail.action_bar.isVisible()

    workspace._show_detail_row(workspace.pod_table, row)
    workspace._opening = True
    workspace._show_view("nodes")
    workspace._opening = False
    assert not workspace.detail.identity_header.isVisible()


def test_refresh_preserves_details_and_decoded_secret_state(qtbot):
    workspace = make_workspace(qtbot)
    row = PodRow("api-1", "shop", "1/1", "Running", 0, "node-a", "1m",
                 {"metadata": {"name": "api-1", "namespace": "shop", "uid": "uid-1"}})
    workspace.pod_table.set_rows([row])
    workspace.pod_table.view.selectRow(0)
    workspace.detail._decoded_keys.add("credential")

    workspace.pod_table.set_rows([PodRow("api-1", "shop", "1/1", "Running", 0, "node-a", "2m",
                                         {"metadata": {"name": "api-1", "namespace": "shop", "uid": "uid-1"}})])
    assert workspace.detail._secret_row is row
    assert workspace.detail._decoded_keys == {"credential"}
    assert workspace.detail.identity_header.isVisible()


def test_panes_menu_restores_hidden_docks_and_log_dock_uses_a_modest_initial_size(qtbot):
    workspace = make_workspace(qtbot)
    actions = {action.text(): action for action in workspace.panes_menu.actions()}
    assert {"Details", "Logs && activity"} <= set(actions)

    workspace.detail_dock.hide()
    actions["Details"].trigger()
    assert workspace.detail_dock.isVisible()
    workspace.detail_dock.hide()
    assert not workspace.detail_dock.isVisible()  # selecting a resource respects the user's hidden state
    row = PodRow("api-1", "shop", "1/1", "Running", 0, "node-a", "1m",
                 {"metadata": {"name": "api-1", "namespace": "shop", "uid": "uid-1"}})
    workspace.pod_table.set_rows([row])
    workspace.pod_table.view.selectRow(0)
    assert not workspace.detail_dock.isVisible()

    workspace._show_log_dock()
    qtbot.waitUntil(lambda: workspace.log_dock.height() >= 100)
    first_height = workspace.log_dock.height()
    assert workspace.log_dock.isVisible() and first_height <= int(workspace.height() * 0.4)
    workspace.log_dock.hide()
    workspace._show_log_dock()
    assert workspace.log_dock.height() == first_height


def test_details_dock_cap_scales_down_for_a_small_window(qtbot):
    workspace = make_workspace(qtbot)
    workspace.resize(500, 360)
    workspace._limit_dock()
    assert workspace.detail_dock.maximumWidth() <= 250
