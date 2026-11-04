from datetime import datetime, timezone
from types import SimpleNamespace
import json
import shlex
import sys

import pytest
from kubernetes import client
from PySide6.QtCore import QCoreApplication, QEvent, QProcess, QTimer, Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QMenu, QMessageBox

from klotse.k8s.client import KubeService, map_node
from klotse.k8s.models import NodeRow, PodRow
from klotse.ui.command_view import CommandView
from klotse.ui.node_dialogs import DrainDialog, NodeShellDialog
from klotse.ui.main_window import MainWindow
from klotse.ui.resource_table import ResourceTable
from klotse.ui.terminal import node_shell_args
from klotse.ui.workspace import ClusterWorkspace


@pytest.fixture
def workspaces(qtbot):
    from test_app import FakeService
    roots = []
    def create(service, context):
        base = FakeService()
        base.__dict__.update(vars(service))
        base.load_contexts = lambda: ([context], context)
        root = MainWindow(lambda: base)
        roots.append(root)
        qtbot.addWidget(root)
        workspace = root.workspace(context)
        qtbot.waitUntil(lambda: workspace._opened_default and not workspace._loading)
        return workspace
    return create


def node_row(cordoned=False):
    return NodeRow("node-a", "Ready", "worker", "v1", "", 1, 1, "1d",
                   {"metadata": {"uid": "uid-a"}, "spec": {"unschedulable": cordoned}})


@pytest.mark.parametrize("cordoned, label, action", [(False, "Cordon…", "cordon"), (True, "Uncordon…", "uncordon")])
def test_node_menu_actions_target_selected_node(qtbot, monkeypatch, cordoned, label, action):
    table = ResourceTable([("Name", "name")], "Node")
    qtbot.addWidget(table)
    row = node_row(cordoned)
    table.set_rows([row])
    emitted = []
    table.action_requested.connect(lambda *args: emitted.append(args))
    def select():
        menu = table.findChild(QMenu)
        try:
            actions = {a.text(): a for a in menu.actions()}
            assert {label, "Drain…", "Shell (nsenter)…", "Delete node…", "List pods on this node"} <= actions.keys()
            actions[label].trigger()
        finally:
            menu.close()
    QTimer.singleShot(0, select)
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert emitted == [(action, row)]


def test_node_api_mutations_are_scoped_and_delete_checks_uid():
    calls = []
    service = KubeService()
    service._core = SimpleNamespace(patch_node=lambda *a, **k: calls.append((a, k)),
                                    delete_node=lambda *a, **k: calls.append((a, k)))
    service.cordon_node("node-a", True)
    service.cordon_node("node-a", False)
    service.delete_node("node-a", "uid-a")
    assert calls[0] == (("node-a", {"spec": {"unschedulable": True}}), {"_request_timeout": 15})
    assert calls[1][0][1] == {"spec": {"unschedulable": False}}
    assert calls[2][0] == ("node-a",)
    assert calls[2][1]["body"].preconditions.uid == "uid-a"


def test_cordon_delete_and_cancel(qtbot, monkeypatch, workspaces):
    calls = []
    service = SimpleNamespace(cordon_node=lambda *a: calls.append(("cordon", *a)),
                              delete_node=lambda *a: calls.append(("delete", *a)))
    ws = workspaces(service, "cluster-a")
    monkeypatch.setattr(ClusterWorkspace, "refresh", lambda self: None)
    monkeypatch.setattr(ClusterWorkspace, "_run", lambda self, fn, cb, *args: cb(fn(*args)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    ws.node_table.action_requested.emit("cordon", node_row())
    assert calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.node_table.action_requested.emit("cordon", node_row())
    ws.node_table.action_requested.emit("uncordon", node_row())
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    ws.node_table.action_requested.emit("delete-node", node_row())
    assert calls == [("cordon", "node-a", True), ("cordon", "node-a", False)]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.node_table.action_requested.emit("delete-node", node_row())
    assert calls[-1] == ("delete", "node-a", "uid-a")


def test_drain_options_and_live_activity(qtbot, monkeypatch, workspaces):
    dialog = DrainDialog("cluster-a", "node-a")
    qtbot.addWidget(dialog)
    default = dialog.command()
    assert default[:6] == ["kubectl", "--context", "cluster-a", "--request-timeout=30s", "drain", "node-a"]
    assert "--ignore-daemonsets" in default and "--timeout=10m" in default
    assert "--force" not in default and "--delete-emptydir-data" not in default
    dialog.force.setChecked(True)
    dialog.delete_emptydir.setChecked(True)
    assert {"--force", "--delete-emptydir-data"} <= set(dialog.command())
    assert "--disable-eviction" not in dialog.command()
    ws = workspaces(SimpleNamespace(), "cluster-a")
    started = []
    monkeypatch.setattr(DrainDialog, "exec", lambda d: QDialog.Accepted)
    monkeypatch.setattr(CommandView, "start", lambda view: started.append(view.argv))
    monkeypatch.setattr(ClusterWorkspace, "refresh", lambda self: None)
    ws.node_table.action_requested.emit("drain", node_row())
    assert started == [default]
    assert ws.log_tabs.count() == 1
    ws.node_action("drain", node_row())
    assert ws.log_tabs.count() == 1  # no duplicate concurrent drain
    ws.shutdown()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_command_view_output_and_exit_status(qtbot, exit_code):
    view = CommandView([sys.executable, "-c", f"print('pod evicted'); raise SystemExit({exit_code})"])
    qtbot.addWidget(view)
    done = []
    view.completed.connect(done.append)
    view.start()
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    assert done == [exit_code == 0]
    assert "pod evicted" in view.text.toPlainText()
    assert not view.stop_button.isEnabled()


def test_command_view_stop_and_missing_kubectl(qtbot):
    view = CommandView([sys.executable, "-c", "import time; time.sleep(30)"])
    qtbot.addWidget(view)
    done = []
    view.completed.connect(done.append)
    view.start()
    qtbot.waitUntil(lambda: view.process.state() == QProcess.Running)
    view.stop()
    assert view.process.state() == QProcess.NotRunning
    assert done == [False]
    missing = CommandView(["/does-not-exist/kubectl"])
    qtbot.addWidget(missing)
    missing.start()
    qtbot.waitUntil(lambda: missing._done)
    assert "Could not start" in missing.status.text()


def test_node_shell_targets_context_and_host_namespaces(qtbot, monkeypatch, workspaces):
    import klotse.ui.workspace as module
    import klotse.ui.terminal as terminal
    monkeypatch.setattr(terminal.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    opened = []
    ws = workspaces(SimpleNamespace(), "cluster-a")
    monkeypatch.setattr(NodeShellDialog, "exec", lambda d: QDialog.Accepted)
    monkeypatch.setattr(module, "open_terminal", lambda argv, terminal: opened.append(argv))
    monkeypatch.setattr(ClusterWorkspace, "_run", lambda self, fn, cb, *args: cb(fn(*args)))
    ws.node_table.action_requested.emit("node-shell", node_row())
    assert opened == [node_shell_args("cluster-a", "node-a")]
    command = opened[0]
    assert command[:7] == ["kubectl", "--context", "cluster-a", "--namespace", "default", "run", "nsenter-aaaaaaaaaaaa"]
    assert {"--restart=Never", "--rm", "-it", "--pod-running-timeout=1m"} <= set(command)
    spec = json.loads(command[command.index("--overrides") + 1])["spec"]
    assert spec["nodeName"] == "node-a"
    assert spec["hostPID"] is spec["hostIPC"] is spec["hostNetwork"] is True
    assert spec["containers"] == [{
        "name": "nsenter", "image": "docker.io/library/alpine",
        "securityContext": {"privileged": True},
        "stdin": True, "stdinOnce": True, "tty": True,
        "command": ["nsenter", "--target", "1", "--mount", "--uts", "--ipc",
                    "--net", "--pid", "bash", "-l"],
        "resources": {}, "volumeMounts": [],
    }]
    assert spec["imagePullSecrets"] is None
    assert spec["volumes"] == []
    assert spec["tolerations"] == [
        {"key": "CriticalAddonsOnly", "operator": "Exists"},
        {"effect": "NoExecute", "operator": "Exists"},
    ]


def test_node_shell_custom_options_and_unique_pods():
    node = 'node-with-"quotes'
    argv = node_shell_args("other-context", node, "ops", "registry.example/nsenter:custom")
    assert argv[:6] == ["kubectl", "--context", "other-context", "--namespace", "ops", "run"]
    assert argv[6] != node_shell_args("other-context", node)[6]
    assert argv[argv.index("--image") + 1] == "registry.example/nsenter:custom"
    spec = json.loads(argv[argv.index("--overrides") + 1])["spec"]
    assert spec["nodeName"] == node
    assert spec["containers"][0]["image"] == "registry.example/nsenter:custom"


def test_macos_terminal_command_is_quoted(monkeypatch):
    import klotse.ui.terminal as terminal
    calls = []
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    monkeypatch.setattr(terminal.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    argv = node_shell_args("context with spaces;echo nope", "node-a")
    terminal.open_terminal(argv)
    assert calls[0][0] == "osascript"
    wrapper = shlex.split(calls[0][-1])
    assert wrapper[:2] == ["exec", sys.executable]
    assert json.loads(wrapper[4]) == argv
    assert argv[2] not in calls[0][2]  # command data never becomes AppleScript source


def test_list_node_pods_uses_server_side_selector_and_pagination(monkeypatch):
    import klotse.k8s.client as module
    calls = []
    def listing(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(items=[len(calls)], metadata=SimpleNamespace(_continue="next" if len(calls) == 1 else None))
    service = KubeService()
    service._core = SimpleNamespace(list_pod_for_all_namespaces=listing)
    monkeypatch.setattr(module, "map_pod", lambda p: p)
    assert service.list_node_pods("node-a") == [1, 2]
    assert all(c["field_selector"] == "spec.nodeName=node-a" for c in calls)
    assert [c["_continue"] for c in calls] == [None, "next"]


def test_node_pods_view_is_across_namespaces_and_keeps_pod_actions(qtbot, monkeypatch, workspaces):
    calls, actions = [], []
    pods = [PodRow("web", "team-a", "1/1", "Running", 0, "node-a", "1d", {"kind": "Pod"})]
    def listing(node):
        calls.append(node)
        return pods
    ws = workspaces(SimpleNamespace(list_node_pods=listing), "cluster-a")
    monkeypatch.setattr(ClusterWorkspace, "pod_action", lambda self, *args: actions.append(args))
    ws.ns_combo.addItem("unrelated-namespace")
    ws.set_namespace("unrelated-namespace")
    ws.node_table.action_requested.emit("node-pods", node_row())
    table, title = ws.views["node-pods:node-a"]
    qtbot.waitUntil(lambda: table.model.rowCount() == 1)
    assert calls == ["node-a"]
    assert "all namespaces" in title
    assert not ws.loaders[table.view_key][1]
    table.selected.emit(pods[0])
    assert "kind: Pod" in ws.detail.toPlainText()
    table.action_requested.emit("logs", pods[0])
    assert actions == [("logs", pods[0])]
    ws.node_action("node-pods", node_row())
    qtbot.waitUntil(lambda: len(calls) == 2 and not ws._loading)
    assert sum(ws.tabs.widget(i) is table for i in range(ws.tabs.count())) == 1
    ws.shutdown()


@pytest.mark.parametrize("ready, cordoned, extra, expected", [
    ("True", False, [], "Ready"),
    ("False", True, [], "NotReady,SchedulingDisabled"),
    ("Unknown", False, [], "Unknown"),
    (None, True, [], "Unknown,SchedulingDisabled"),
    ("True", True, [("MemoryPressure", "True"), ("DiskPressure", "False"),
                    ("PIDPressure", "Unknown"), ("NetworkUnavailable", "True")],
     "Ready,SchedulingDisabled,MemoryPressure,PIDPressure=Unknown,NetworkUnavailable"),
])
def test_node_status_conditions(ready, cordoned, extra, expected):
    conditions = ([client.V1NodeCondition(type="Ready", status=ready)] if ready else [])
    conditions += [client.V1NodeCondition(type=kind, status=status) for kind, status in extra]
    node = client.V1Node(metadata=client.V1ObjectMeta(name="node-a", creation_timestamp=datetime.now(timezone.utc)),
                         spec=client.V1NodeSpec(unschedulable=cordoned),
                         status=client.V1NodeStatus(conditions=conditions))
    row = map_node(node)
    assert row.status == expected
    assert row.is_ready == (ready == "True")
    assert map_node(node, include_raw=False).status == expected
    from klotse.ui.table_model import TableModel
    model = TableModel([("Status", "status")], search_metadata=True)
    model.set_rows([row])
    tooltip = model.data(model.index(0, 0), Qt.ToolTipRole)
    assert expected in tooltip
    for condition in conditions:
        assert f"{condition.type}: {condition.status}" in tooltip


def test_overview_ready_count_includes_cordoned_ready_nodes():
    svc = KubeService()
    nodes = [client.V1Node(metadata=client.V1ObjectMeta(name="node-a"),
                          spec=client.V1NodeSpec(unschedulable=True), status=client.V1NodeStatus(
                              conditions=[client.V1NodeCondition(type="Ready", status="True")]))]
    svc._core = SimpleNamespace(list_node=lambda **kw: SimpleNamespace(items=nodes, metadata=None),
                                list_namespace=lambda **kw: SimpleNamespace(items=[], metadata=None))
    svc._overview_pods = lambda: ({}, {})
    svc._overview_events = lambda: ([], 0, [])
    assert svc.overview_cluster()["counts"]["nodes_ready"] == 1
