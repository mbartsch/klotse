from types import SimpleNamespace

from kubeman.k8s.client import KubeService
from kubeman.k8s.models import PodRow
from kubeman.ui.pod_view import container_rows, pod_containers
from kubeman.ui.main_window import MainWindow
from kubeman.ui.resource_table import ResourceTable

from test_app import FakeService, FakeStream


def pod():
    return PodRow("web", "team-a", "1/2", "Running", 1, "node-a", "1d", {
        "kind": "Pod", "spec": {
            "containers": [{"name": "app", "image": "app:1"}, {"name": "sidecar", "image": "proxy:2"}],
            "initContainers": [{"name": "setup", "image": "setup:1"}],
            "ephemeralContainers": [{"name": "debugger", "image": "debug:1"}],
        }, "status": {"containerStatuses": [
            {"name": "app", "ready": True, "restartCount": 1, "state": {"running": {}}},
            {"name": "sidecar", "ready": False, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}],
            "initContainerStatuses": [{"name": "setup", "state": {"terminated": {"reason": "Completed"}}}]}})


def test_container_rows_include_all_types_with_their_spec():
    row = pod()
    assert pod_containers(row.raw)[:3] == [
        ("app", "Container", "Running", "Yes", "1", "app:1"),
        ("sidecar", "Container", "CrashLoopBackOff", "No", "0", "proxy:2"),
        ("setup", "Init", "Completed", "No", "0", "setup:1")]
    rows = container_rows(row.raw)
    assert [r.name for r in rows] == ["app", "sidecar", "setup", "debugger"]
    assert [r.type for r in rows] == ["Container", "Container", "Init", "Ephemeral"]
    assert rows[1].raw == {"name": "sidecar", "image": "proxy:2"} and rows[1].state == "CrashLoopBackOff"


def test_pod_double_click_uses_proxy_row(qtbot):
    from PySide6.QtCore import Qt
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    first, second = pod(), pod()
    first.name, second.name = "alpha", "zeta"
    table.set_rows([first, second])
    table.proxy.sort(0, Qt.DescendingOrder)
    selected = []
    table.action_requested.connect(lambda *args: selected.append(args))
    table.view.doubleClicked.emit(table.proxy.index(0, 0))
    assert selected == [("containers", second)]


def test_force_delete_api_options_keep_node_uid():
    service = KubeService()
    calls = []
    service._core = SimpleNamespace(delete_namespaced_pod=lambda *a, **k: calls.append((a, k)),
                                    delete_node=lambda *a, **k: calls.append((a, k)))
    service.delete_pod("web", "team-a")
    service.delete_pod("web", "team-a", True)
    service.delete_node("node-a", "uid-a", True)
    assert "body" not in calls[0][1]
    assert calls[1][1]["body"].grace_period_seconds == 0
    assert calls[1][1]["body"].propagation_policy == "Background"
    assert calls[2][1]["body"].grace_period_seconds == 0
    assert calls[2][1]["body"].preconditions.uid == "uid-a"


def test_workspace_container_logs_shell_and_force_delete(qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox
    from kubeman.ui import workspace as module
    calls, opened, logs = [], [], []
    service = FakeService()
    service.delete_pod = lambda *a: calls.append(("pod", *a))
    service.delete_node = lambda *a: calls.append(("node", *a))
    def open_logs(*args):
        logs.append(args)
        return FakeStream()
    service.open_log_stream = open_logs
    window = MainWindow(lambda: service)
    qtbot.addWidget(window)
    ws = window.workspace("ctx")
    qtbot.waitUntil(lambda: ws._opened_default and not ws._loading)
    row = pod()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.pod_action("force-terminate", row)
    node = SimpleNamespace(name="node-a", raw={"metadata": {"uid": "uid-a"}})
    ws.node_action("force-delete-node", node)
    qtbot.waitUntil(lambda: len(calls) == 2)
    assert ("pod", "web", "team-a", True) in calls
    assert ("node", "node-a", "uid-a", True) in calls
    ws.pod_action("containers", row)  # double-click on a pod: its own tab, not a popup
    tab = ws._pod_tabs["pod:team-a/web"]
    assert ws.foreground_key() == "pod:team-a/web" and tab["table"].model.rowCount() == 4
    tab["table"].action_requested.emit("logs", tab["table"].model.rows[1])  # the "sidecar" container
    qtbot.waitUntil(lambda: bool(logs))
    assert logs[0][:3] == ("team-a", "web", "sidecar")
    assert ws.log_tabs.currentWidget().container.currentText() == "sidecar"
    monkeypatch.setattr(module, "open_terminal", opened.append)
    ws._container_action("shell", row, "app")
    qtbot.waitUntil(lambda: bool(opened))
    assert opened[0][-4:] == ["-c", "app", "--", "sh"]
    assert opened[0][opened[0].index("-c") + 1] == "app"
    assert opened[0][2] == "ctx"
    qtbot.waitUntil(lambda: not ws._workers)
