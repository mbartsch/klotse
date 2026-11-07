from types import SimpleNamespace

from klotse.k8s import capacity
from klotse.k8s.models import NodeRow, PodRow
from klotse.ui.capacity_dialog import CapacityDialog, number, percent_text
from klotse.ui.workspace import ClusterWorkspace


def node(name, cpu=4.0, mem=16.0, used=-1.0, mem_used=-1.0):
    return NodeRow(name, "Ready", "worker", "v1", "1.1.1.1", cpu, mem, "1d", {}, used, mem_used)


def pod(name, ns, on, cpu="500m", mem="512Mi", limit_cpu="1", phase="Running", used=-1.0, mem_used=-1.0):
    resources = {"requests": {"cpu": cpu, "memory": mem}, "limits": {"cpu": limit_cpu, "memory": "1Gi"}}
    raw = {"spec": {"containers": [{"name": "c", "resources": resources}]}, "status": {"phase": phase}}
    return PodRow(name, ns, "1/1", phase, 0, on, "1d", raw, used, mem_used)


def test_by_node_sums_requests_and_limits_and_skips_finished_pods():
    pods = [pod("a", "x", "n1"), pod("b", "y", "n1", cpu="1"), pod("c", "x", "n2"), pod("done", "x", "n1", phase="Succeeded")]
    rows = {r["name"]: r for r in capacity.by_node([node("n1"), node("n2")], pods)}
    n1 = rows["n1"]
    assert n1["pods"] == 2 and n1["cpu_req"] == 1.5 and n1["cpu_lim"] == 2.0 and n1["mem_req"] == 1.0
    assert n1["cpu_req_pct"] == 37.5 and n1["mem_req_pct"] == 6.25
    assert rows["n2"]["pods"] == 1 and rows["n2"]["cpu_used"] == -1.0


def test_usage_uses_the_node_figure_else_the_pods_and_stays_unknown_without_metrics():
    pods = [pod("a", "x", "n1", used=0.25, mem_used=256.0), pod("b", "x", "n1", used=0.25, mem_used=256.0)]
    from_pods = capacity.by_node([node("n1")], pods)[0]
    assert from_pods["cpu_used"] == 0.5 and from_pods["mem_used"] == 0.5
    from_node = capacity.by_node([node("n1", used=2.0, mem_used=4.0)], pods)[0]
    assert from_node["cpu_used"] == 2.0 and from_node["mem_used"] == 4.0


def test_by_namespace_and_totals():
    pods = [pod("a", "x", "n1"), pod("b", "x", "n2"), pod("c", "y", "n1")]
    spaces = {r["name"]: r for r in capacity.by_namespace(pods)}
    assert spaces["x"]["pods"] == 2 and spaces["x"]["cpu_req"] == 1.0 and spaces["y"]["pods"] == 1
    total = capacity.totals(capacity.by_node([node("n1"), node("n2")], pods))
    assert total["cpu_alloc"] == 8.0 and total["cpu_req"] == 1.5 and round(total["cpu_req_pct"], 2) == 18.75


def test_zero_allocatable_is_not_a_percentage():
    assert capacity.percent(1.0, 0.0) == -1.0 and capacity.percent(-1.0, 4.0) == -1.0


def test_number_and_percent_text():
    assert number(-1) == "" and number(0) == "0" and number(1.5) == "1.5" and number(2.0) == "2"
    assert percent_text(-1) == "" and percent_text(80) == "80%" and percent_text(120) == "120% over"


def test_dialog_fills_both_tables_and_sorts_by_number(qtbot):
    dialog = CapacityDialog("ctx")
    qtbot.addWidget(dialog)
    pods = [pod("a", "x", "n1"), pod("b", "y", "n2", cpu="2")]
    dialog.show_data([node("n1"), node("n2")], pods)
    assert dialog.nodes.rowCount() == 2 and dialog.spaces.rowCount() == 2
    assert "CPU 2.5 / 8 cores" in dialog.summary.text()
    dialog.nodes.sortItems(3)  # CPU req ascending
    assert dialog.nodes.item(0, 0).text() == "n1" and dialog.nodes.item(1, 0).text() == "n2"
    dialog.nodes.sortItems(3, Qt_desc())
    assert dialog.nodes.item(0, 0).text() == "n2"
    dialog.show_error("boom")
    assert "boom" in dialog.summary.text()


def Qt_desc():
    from PySide6.QtCore import Qt
    return Qt.DescendingOrder


def test_workspace_loads_the_dialog_from_the_service(qtbot):
    service = SimpleNamespace(list_nodes=lambda: [node("n1")], list_pods=lambda ns=None: [pod("a", "x", "n1")])
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_capacity()
    qtbot.waitUntil(lambda: dialog.nodes.rowCount() == 1, timeout=5000)
    assert dialog.nodes.item(0, 0).text() == "n1" and ws.show_capacity() is dialog
    ws.shutdown()
    qtbot.waitUntil(lambda: not ws._workers)
