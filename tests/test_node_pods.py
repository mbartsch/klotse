"""Pods per node: all nodes counted on first load, rows on screen refreshed on sight, everything every minute."""
from types import SimpleNamespace as NS

from kubeman.k8s.client import KubeService
from kubeman.k8s.models import NodeRow
from kubeman.ui import workspace as workspace_module
from kubeman.ui.workspace import ClusterWorkspace


def node(name):
    return NodeRow(name, "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"metadata": {"uid": name}})


class CountingService:
    def __init__(self, per_node=7):
        self.all_calls, self.node_calls, self.per_node = 0, [], per_node

    def all_node_pod_counts(self):
        self.all_calls += 1
        return {f"n{i:02d}": self.per_node for i in range(0, 200, 2)}  # odd nodes have no pods: absent from the pass

    def node_pod_counts(self, names):
        self.node_calls.append(list(names))
        return {n: self.per_node + 1 for n in names}


def make_ws(qtbot, service):
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws._ready = True
    ws.resize(700, 300)
    ws.load_view = lambda key: None  # no real loads: the tests feed rows through _set_nodes
    ws.show()
    ws.open_view("nodes")
    return ws


def idle(qtbot, ws):
    qtbot.waitUntil(lambda: not ws._workers and not ws._node_pods_busy, timeout=5000)


def pods_cell(ws, row):
    col = [c[0] for c in ws.node_table.model.columns].index("Pods")
    return ws.node_table.model.index(row, col).data()


def test_first_load_counts_every_node_in_one_pass(qtbot):
    svc = CountingService()
    ws = make_ws(qtbot, svc)
    ws._set_nodes([node(f"n{i:02d}") for i in range(200)])
    idle(qtbot, ws)
    assert svc.all_calls == 1
    assert pods_cell(ws, 0) == "7" and pods_cell(ws, 199) == "0"  # even off screen; no pods counts as 0
    assert svc.node_calls == []  # nothing was asked per node: the full pass already covered the visible rows


def test_reload_keeps_numbers_and_does_not_recount_everything(qtbot):
    svc = CountingService()
    ws = make_ws(qtbot, svc)
    ws._set_nodes([node(f"n{i:02d}") for i in range(200)])
    idle(qtbot, ws)
    ws._set_nodes([node(f"n{i:02d}") for i in range(200)])  # the 10 s reload
    assert pods_cell(ws, 0) == "7"  # no blanking while it is recounted
    qtbot.wait(600)
    idle(qtbot, ws)
    assert svc.all_calls == 1  # not re-counted wholesale
    ws._set_nodes([node(f"n{i:02d}") for i in range(200)] + [node("n200")])  # a new node joined
    idle(qtbot, ws)
    assert svc.all_calls == 2


def test_rows_coming_into_sight_are_recounted_only_when_stale(qtbot, monkeypatch):
    svc = CountingService()
    ws = make_ws(qtbot, svc)
    table = ws.node_table
    ws._set_nodes([node(f"n{i:02d}") for i in range(200)])
    idle(qtbot, ws)
    svc.node_calls.clear()
    ws._update_node_pods()
    assert svc.node_calls == []  # everything on screen was counted a moment ago

    monkeypatch.setattr(workspace_module, "NODE_PODS_TTL_S", -1)  # pretend the counts are old
    visible = [r.name for r in table.visible_rows()]
    ws._update_node_pods()
    idle(qtbot, ws)
    assert svc.node_calls == [visible] and 0 < len(visible) < 60  # only the rows on screen, not all 200
    assert pods_cell(ws, 0) == "8" and pods_cell(ws, 150) == "7"  # off-screen rows keep the older number

    svc.node_calls.clear()
    table.view.scrollToBottom()
    qtbot.waitUntil(lambda: bool(svc.node_calls), timeout=3000)
    idle(qtbot, ws)
    assert "n199" in svc.node_calls[0] and not set(svc.node_calls[0]) & set(visible)


def test_minute_tick_recounts_all_nodes_only_when_allowed(qtbot):
    svc = CountingService()
    ws = make_ws(qtbot, svc)
    ws._set_nodes([node(f"n{i:02d}") for i in range(10)])
    idle(qtbot, ws)
    assert ws._node_pods_timer.interval() == 60_000
    ws._refresh_all_node_pods()
    idle(qtbot, ws)
    assert svc.all_calls == 2

    ws.auto_action.setChecked(False)  # auto-refresh off: no timer, no background recount
    assert not ws._node_pods_timer.isActive()
    ws._refresh_all_node_pods()
    assert svc.all_calls == 2

    ws.auto_action.setChecked(True)
    assert ws._node_pods_timer.isActive()
    ws.hide()  # cluster not showing: skipped
    ws._refresh_all_node_pods()
    idle(qtbot, ws)
    assert svc.all_calls == 2
    ws.show()
    ws._refresh_all_node_pods()
    idle(qtbot, ws)
    assert svc.all_calls == 3
    ws.shutdown()
    assert not ws._node_pods_timer.isActive()


def test_full_count_requested_while_busy_runs_afterwards(qtbot):
    svc = CountingService()
    ws = make_ws(qtbot, svc)
    ws._set_nodes([node("n00")])
    ws._count_node_pods(None)  # asked again while the first is still running
    idle(qtbot, ws)
    qtbot.waitUntil(lambda: svc.all_calls == 2 and not ws._node_pods_busy, timeout=5000)


def test_service_without_counting_support_leaves_column_blank(qtbot):
    ws = make_ws(qtbot, NS())
    ws._set_nodes([node("a")])
    assert not ws._workers and pods_cell(ws, 0) == "--"  # never counted: shown as unknown


def test_all_node_pod_counts_service_one_pass_skips_unscheduled():
    seen = []

    def listing(**kw):
        seen.append(kw["field_selector"])
        pod = lambda node: NS(spec=NS(node_name=node))  # noqa: E731
        return NS(items=[pod("a"), pod("a"), pod("b"), pod(None), pod("")], metadata=None)

    svc = KubeService()
    svc._core = NS(list_pod_for_all_namespaces=listing)
    assert svc.all_node_pod_counts() == {"a": 2, "b": 1}
    assert seen == ["status.phase!=Succeeded,status.phase!=Failed"]  # a single call, finished pods excluded


def test_node_pod_counts_service_counts_unfinished_pods_per_node():
    seen = []

    def listing(**kw):
        seen.append(kw["field_selector"])
        if "bad" in kw["field_selector"]:
            raise RuntimeError("boom")
        n = 3 if "n1" in kw["field_selector"] else 0
        return NS(items=[object()] * n, metadata=None)

    svc = KubeService()
    svc._core = NS(list_pod_for_all_namespaces=listing)
    assert svc.node_pod_counts(["n1", "n2", "bad"]) == {"n1": 3, "n2": 0}  # a failing node is skipped
    assert seen[0] == "spec.nodeName=n1,status.phase!=Succeeded,status.phase!=Failed"
