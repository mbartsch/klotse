from types import SimpleNamespace

from PySide6.QtWidgets import QToolButton

from klotse.ui.detail_panel import DetailPanel
from klotse.ui.resource_table import ResourceTable
from klotse.k8s.models import PodRow
from klotse.ui.workspace import ClusterWorkspace


def test_deployment_details_promote_scale_actions_and_keep_other_actions_in_overflow(qtbot):
    table = ResourceTable([("Name", "name")], "Deployment")
    row = SimpleNamespace(name="api", namespace="shop", raw={"metadata": {"name": "api", "namespace": "shop"}})
    calls = []
    table.scale_requested.connect(lambda kind, selected: calls.append(("scale", kind, selected)))
    table.restart_requested.connect(lambda kind, selected: calls.append(("restart", kind, selected)))

    specs = table.row_actions(row)
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.set_object_actions(specs)
    direct = [button for button in panel.action_bar.findChildren(QToolButton) if button.text() != "More…"]
    assert [button.text() for button in direct] == ["Edit…", "Scale…"]
    next(button for button in direct if button.text() == "Scale…").click()
    assert calls == [("scale", "Deployment", row)]

    more = next(button for button in panel.action_bar.findChildren(QToolButton) if button.text() == "More…")
    menu_actions = {action.text(): action for action in more.menu().actions() if not action.isSeparator()}
    assert {"Restart…", "Copy as new…", "Logs of all pods…", "Rollout history / Undo…"} <= set(menu_actions)
    menu_actions["Restart…"].trigger()
    assert calls[-1] == ("restart", "Deployment", row)


def test_pod_actions_keep_permission_hints_and_specialized_actions(qtbot):
    table = ResourceTable([("Name", "name")], "Pod")
    table.permission_check = lambda _kind, action, _row: action != "logs"
    row = SimpleNamespace(name="api-1", namespace="shop", secret_data={}, raw={"metadata": {"name": "api-1"}})
    specs = table.row_actions(row)
    by_key = {action.key: action for action in specs}
    assert by_key["logs"].title == "Logs  (not allowed for you)"
    assert by_key["logs"].primary
    assert by_key["port-forward"].title == "Port forward…"
    assert by_key["terminate"].dangerous and not by_key["terminate"].primary

    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.set_object_actions(specs)
    assert panel.action_bar.isVisible() is False  # the parent header is hidden until a row is shown
    panel.show_row(row)
    panel.set_object_actions(specs)
    assert panel.action_bar.isVisible()
    assert any("not allowed for you" in button.text() for button in panel.action_bar.findChildren(QToolButton))


def test_workspace_selection_populates_the_details_action_header(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    row = PodRow("api-1", "shop", "1/1", "Running", 0, "node-a", "1m",
                 {"metadata": {"name": "api-1", "namespace": "shop", "uid": "uid-1"}})
    ws.pod_table.set_rows([row])
    ws.pod_table.view.selectRow(0)

    assert ws.detail.identity_title.text() == "Pod · api-1"
    assert ws.detail.identity_scope.text() == "Namespace: shop"
    assert ws.detail.action_bar.isVisible()
    assert [button.text() for button in ws.detail.action_bar.findChildren(QToolButton)
            if button.text() != "More…"] == ["Edit…", "Logs"]
