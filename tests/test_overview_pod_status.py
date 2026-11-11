from kubernetes import client

from klotse.k8s.client import map_pod, pod_bucket
from klotse.k8s.models import PodRow
from klotse.ui.overview import CLUSTER_CARDS, OverviewView
from klotse.ui.resource_table import ResourceTable
from klotse.ui.workspace import ClusterWorkspace


def _pod(name, status, namespace="team-a"):
    return PodRow(name, namespace, "0/1", status, 0, "node-a", "1m", {})


def test_missing_pod_phase_maps_to_failed_unknown_status():
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name="unknown", namespace="team-a"),
        spec=client.V1PodSpec(containers=[]),
        status=client.V1PodStatus(phase=""),
    )

    row = map_pod(pod)
    assert row.status == "Unknown"
    assert pod_bucket(row.status) == "failed"


def test_overview_pod_status_actions_are_explicit_and_disabled_at_zero(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    view.set_data({
        "counts": {"pods": 3, "running": 2, "pending": 0, "failed": 1},
        "events": [],
        "lists": {"pending": [], "failed": [_pod("broken", "CrashLoopBackOff")]},
    })

    assert view.pod_status_actions["pending"].text() == "View pending pods"
    assert not view.pod_status_actions["pending"].isEnabled()
    assert view.pod_status_actions["failed"].text() == "View failed pods"
    assert view.pod_status_actions["failed"].isEnabled()
    assert any(label.text() == "Running pods" for label in view.findChildren(type(view.workload_rows["running"][1])))

    view.set_data({
        "counts": {"pods": 3, "running": 2, "pending": 1, "failed": 0},
        "events": [],
        "lists": {"pending": [_pod("waiting", "ContainerCreating")], "failed": []},
    })
    assert view.pod_status_actions["pending"].isEnabled()
    assert not view.pod_status_actions["failed"].isEnabled()


def test_overview_pod_actions_open_scoped_pods_list_with_visible_filters(qtbot):
    workspace = ClusterWorkspace(None, "test")
    qtbot.addWidget(workspace)
    scope = "team-a"
    workspace.namespace = lambda: scope
    opened = []
    workspace.open_view = lambda key: opened.append((key, workspace.namespace()))

    workspace._overview_action("pod-status:pending")
    pending_filter = workspace.pod_table.filter.text()
    assert pending_filter == "Status ~ '^(Pending|ContainerCreating|PodInitializing|Terminating|Init:.*)$'"
    assert opened == [("pods", scope)]

    pending_statuses = ["Pending", "ContainerCreating", "PodInitializing", "Terminating", "Init:0/1",
                        "Running", "Succeeded", "CrashLoopBackOff", "Unknown"]
    table = ResourceTable([("Name", "name"), ("Status", "status")], "Pod", editable=False, customizable=False)
    qtbot.addWidget(table)
    table.set_rows([_pod(str(i), status) for i, status in enumerate(pending_statuses)])
    table.filter.setText(pending_filter)
    table._apply_filter()
    pending_rows = [table.model.rows[table.proxy.mapToSource(table.proxy.index(i, 0)).row()]
                    for i in range(table.proxy.rowCount())]
    assert [row.status for row in pending_rows] == pending_statuses[:5]

    workspace._overview_action("pod-status:failed")
    failed_filter = workspace.pod_table.filter.text()
    assert failed_filter == "Status !~ '^(Running|Succeeded|Completed|Pending|ContainerCreating|PodInitializing|Terminating|Init:.*)$'"
    assert opened[-1] == ("pods", scope)
    table.filter.setText(failed_filter)
    table._apply_filter()
    failed_rows = [table.model.rows[table.proxy.mapToSource(table.proxy.index(i, 0)).row()]
                   for i in range(table.proxy.rowCount())]
    assert [row.status for row in failed_rows] == ["CrashLoopBackOff", "Unknown"]


def test_big_cluster_status_actions_filter_by_raw_phase(qtbot):
    workspace = ClusterWorkspace(None, "test")
    qtbot.addWidget(workspace)
    workspace.namespace = lambda: "team-a"
    workspace.open_view = lambda _key: None
    workspace.overview._data = {"counts": {"by_phase": True}}

    rows = [
        PodRow("failed-waiting", "team-a", "0/1", "ContainerCreating", 0, "node-a", "1m",
               {"status": {"phase": "Failed"}}),
        PodRow("pending-crashloop", "team-a", "0/1", "CrashLoopBackOff", 0, "node-a", "1m",
               {"status": {"phase": "Pending"}}),
        PodRow("healthy", "team-a", "1/1", "Running", 0, "node-a", "1m",
               {"status": {"phase": "Running"}}),
    ]
    table = ResourceTable([("Name", "name"), ("Status", "status")], "Pod", editable=False, customizable=False)
    qtbot.addWidget(table)
    table.set_rows(rows)

    workspace._overview_action("pod-status:failed")
    failed_filter = workspace.pod_table.filter.text()
    assert failed_filter == "status.phase = Failed"
    table.filter.setText(failed_filter)
    table._apply_filter()
    failed_rows = [table.model.rows[table.proxy.mapToSource(table.proxy.index(i, 0)).row()]
                   for i in range(table.proxy.rowCount())]
    assert [row.name for row in failed_rows] == ["failed-waiting"]

    workspace._overview_action("pod-status:pending")
    pending_filter = workspace.pod_table.filter.text()
    assert pending_filter == "status.phase = Pending"
    table.filter.setText(pending_filter)
    table._apply_filter()
    pending_rows = [table.model.rows[table.proxy.mapToSource(table.proxy.index(i, 0)).row()]
                    for i in range(table.proxy.rowCount())]
    assert [row.name for row in pending_rows] == ["pending-crashloop"]
