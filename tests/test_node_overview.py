"""The overview at the top of a node's tab: status, CPU, memory and pods at a glance."""
from types import SimpleNamespace

import pytest

from klotse.k8s.models import NodeRow, PodRow
from klotse.ui.node_bar import NodeActionBar
from klotse.ui.node_overview import NodeOverview, active_pods, bar_text, percent, summarize
from klotse.ui.workspace import ClusterWorkspace

RAW = {"metadata": {"name": "n1", "labels": {"node.kubernetes.io/instance-type": "m5.large", "topology.kubernetes.io/zone": "eu-west-1a"}},
       "spec": {"taints": [{"key": "dedicated", "value": "web", "effect": "NoSchedule"}, {"key": "node.kubernetes.io/unreachable", "effect": "NoExecute"}]},
       "status": {"allocatable": {"pods": "110"}}}


def node(status="Ready", cpu_used=-1.0, mem_used=-1.0, raw=RAW):
    return NodeRow("n1", status, "worker", "v1.30.1", "10.0.0.5", 4.0, 8.0, "3d", raw, cpu_used, mem_used)


def pod(name, cpu="500m", memory="256Mi", phase="Running"):
    raw = {"metadata": {"name": name}, "spec": {"containers": [{"name": "c", "resources": {"requests": {"cpu": cpu, "memory": memory}}}]},
           "status": {"phase": phase}}
    return PodRow(name, "shop", "1/1", phase, 0, "n1", "1d", raw)


# ---- the numbers -------------------------------------------------------------------------------------------------
def test_requests_of_the_pods_are_summed_and_finished_pods_are_left_out():
    pods = [pod("a"), pod("b", cpu="1", memory="1Gi"), pod("done", phase="Succeeded"), pod("failed", phase="Failed")]
    s = summarize(node(cpu_used=1.2, mem_used=3.0), pods)
    assert s["cpu_req"] == pytest.approx(1.5) and s["mem_req"] == pytest.approx((256 + 1024) / 1024)
    assert (s["cpu_alloc"], s["mem_alloc"], s["cpu_used"], s["mem_used"]) == (4.0, 8.0, 1.2, 3.0)
    assert s["pods"] == 2 and s["pod_limit"] == 110 and s["pods_known"]
    assert [p.name for p in active_pods(pods)] == ["a", "b"]


def test_the_facts_and_taints_come_from_the_node():
    s = summarize(node(), [])
    assert s["facts"] == ["worker", "v1.30.1", "10.0.0.5", "m5.large", "eu-west-1a"]
    assert s["taints"] == ["dedicated=web:NoSchedule", "node.kubernetes.io/unreachable:NoExecute"]
    bare = summarize(NodeRow("n", "Ready", "<none>", "", "", 1.0, 1.0, "1d", {}), [])
    assert bare["facts"] == [] and bare["taints"] == [] and bare["pod_limit"] == 0


def test_status_has_a_tone_that_is_never_green_for_a_cordoned_or_failing_node():
    assert summarize(node("Ready"), [])["tone"] == "ok"
    assert summarize(node("Ready,SchedulingDisabled"), [])["tone"] != "ok"
    assert summarize(node("NotReady"), [])["tone"] == "bad"


def test_unknown_pods_and_unknown_metrics_stay_unknown():
    s = summarize(node(), None)
    assert not s["pods_known"] and s["cpu_used"] == -1.0 and s["pods"] == 0


@pytest.mark.parametrize("part, whole, expected", [(1, 4, 25.0), (0, 4, 0.0), (5, 4, 125.0), (-1, 4, -1.0), (1, 0, -1.0)])
def test_percent(part, whole, expected):
    assert percent(part, whole) == expected


def test_bar_text_writes_the_numbers_and_the_percentage_and_flags_overcommit():
    assert bar_text("requested", 3.2, 4, "cores") == "requested 3.2 cores / 4 cores (80%)"
    assert bar_text("requested", 5, 4, "cores").endswith("(125%) · overcommitted")
    assert bar_text("in use", 0, 0, "cores") == "in use: n/a"
    assert bar_text("requested", 250, 1000, "x") == "requested 250 x / 1000 x (25%)"


# ---- the widget --------------------------------------------------------------------------------------------------
def test_the_widget_writes_every_value_out_in_words(qtbot):
    w = NodeOverview()
    qtbot.addWidget(w)
    w.show()
    w.set_summary(summarize(node("Ready,SchedulingDisabled", cpu_used=1.0, mem_used=2.0), [pod("a", "1", "2Gi"), pod("b", "1", "2Gi")]))
    assert "SchedulingDisabled" in w.status.text() and ("▲" in w.status.text() or "✖" in w.status.text())  # symbol and words
    cpu_req, cpu_used = w.bars["cpu"]
    assert cpu_req.format() == "requested 2 cores / 4 cores (50%)" and cpu_req.value() == 50
    assert cpu_used.format() == "in use 1 cores / 4 cores (25%)"
    mem_req, mem_used = w.bars["memory"]
    assert mem_req.format() == "requested 4 GiB / 8 GiB (50%)" and mem_used.format() == "in use 2 GiB / 8 GiB (25%)"
    assert w.bars["pods"][0].format() == "2 of 110 pods" and not w.bars["pods"][1].isVisible()
    assert w.facts.text().startswith("worker · v1.30.1") and w.taints.text().startswith("Taints: dedicated=web:NoSchedule")


def test_without_metrics_the_usage_bar_says_so_and_loading_pods_say_loading(qtbot):
    w = NodeOverview()
    qtbot.addWidget(w)
    w.set_summary(summarize(node(), None))
    assert w.bars["cpu"][1].format() == "in use: unknown (no metrics)" and w.bars["cpu"][1].value() == 0
    assert w.bars["cpu"][0].format() == "requested: loading…"


def test_a_value_over_the_whole_fills_the_bar_but_not_beyond(qtbot):
    w = NodeOverview()
    qtbot.addWidget(w)
    w.set_summary(summarize(node(), [pod("big", cpu="8")]))
    bar = w.bars["cpu"][0]
    assert bar.value() == 100 and "200%" in bar.format() and "overcommitted" in bar.format()


def test_taints_beyond_three_are_counted(qtbot):
    raw = {"spec": {"taints": [{"key": f"k{i}", "effect": "NoSchedule"} for i in range(5)]}}
    w = NodeOverview()
    qtbot.addWidget(w)
    w.set_summary(summarize(node(raw=raw), []))
    assert w.taints.text() == "Taints: k0:NoSchedule, k1:NoSchedule, k2:NoSchedule +2"


# ---- in the bar and the workspace --------------------------------------------------------------------------------
def test_the_bar_shows_the_overview_once_the_node_is_known_and_follows_the_pods(qtbot):
    bar = NodeActionBar("n1")
    qtbot.addWidget(bar)
    bar.show()
    assert not bar.overview.isVisible()  # no node row yet
    bar.set_row(node())
    assert bar.overview.isVisible() and bar.overview.bars["cpu"][0].format() == "requested: loading…"
    bar.set_pods([pod("a", "2")])
    assert bar.overview.bars["cpu"][0].format() == "requested 2 cores / 4 cores (50%)"
    bar.set_pods([pod("a", "2"), pod("b", "1")])
    assert bar.overview.bars["cpu"][0].value() == 75
    bar.set_row(None, gone=True)
    assert not bar.overview.isVisible()  # the node is gone: nothing to summarise


def test_opening_a_nodes_tab_fills_the_overview_from_its_pods(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(list_node_pods=lambda name: [pod("a", "1"), pod("b", "1", phase="Succeeded")]), "ctx")
    qtbot.addWidget(ws)
    ws.node_table.set_rows([node(cpu_used=0.5)])
    ws.open_node_pods("n1", ws.node_table.model.rows[0])
    bar = ws._node_bars["n1"][0]
    qtbot.waitUntil(lambda: bar.overview.bars["cpu"][0].format().startswith("requested 1 cores"), timeout=5000)
    assert bar.overview.bars["pods"][0].format() == "1 of 110 pods" and bar.overview.bars["cpu"][1].format().startswith("in use 0.5")


def test_a_node_list_reload_updates_the_overview(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(list_node_pods=lambda name: []), "ctx")
    qtbot.addWidget(ws)
    ws.node_table.set_rows([node()])
    ws.open_node_pods("n1", ws.node_table.model.rows[0])
    ws._count_node_pods = lambda *a: None
    ws._set_nodes([node("Ready", cpu_used=2.0)])
    bar = ws._node_bars["n1"][0]
    assert bar.overview.bars["cpu"][1].format() == "in use 2 cores / 4 cores (50%)"
