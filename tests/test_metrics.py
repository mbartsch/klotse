"""Optional metrics-server usage: columns, overview bars, and graceful absence."""
from types import SimpleNamespace as NS

from kubernetes import client as k

from kubeman.k8s.client import KubeService
from kubeman.k8s.models import NodeRow
from kubeman.ui.overview import CLUSTER_CARDS, OverviewView, UsageBar
from kubeman.ui.workspace import ClusterWorkspace


def test_usage_attached_and_degrades_without_metrics_server():
    node = k.V1Node(metadata=k.V1ObjectMeta(name="n1"), status=k.V1NodeStatus(allocatable={"cpu": "4", "memory": "8Gi"}))
    pod = k.V1Pod(metadata=k.V1ObjectMeta(name="web", namespace="ns"), spec=k.V1PodSpec(containers=[]),
                  status=k.V1PodStatus(phase="Running"))
    svc = KubeService()
    svc._core = NS(list_node=lambda **kw: NS(items=[node], metadata=None),
                   list_pod_for_all_namespaces=lambda **kw: NS(items=[pod], metadata=None))

    def metrics(group, version, plural, **kw):
        assert (group, version) == ("metrics.k8s.io", "v1beta1")
        if plural == "nodes":
            return {"items": [{"metadata": {"name": "n1"}, "usage": {"cpu": "500m", "memory": "2Gi"}}]}
        return {"items": [{"metadata": {"name": "web", "namespace": "ns"}, "containers": [
            {"usage": {"cpu": "100m", "memory": "64Mi"}}, {"usage": {"cpu": "50000000n", "memory": "32Mi"}}]}]}

    svc._custom = NS(list_cluster_custom_object=metrics)
    n = svc.list_nodes()[0]
    assert (n.cpu_used, n.mem_used) == (0.5, 2.0)
    p = svc.list_pods()[0]
    assert abs(p.cpu_used - 0.15) < 1e-9 and p.mem_used == 96.0

    def broken(*a, **kw):
        raise k.ApiException(status=404)

    svc._custom = NS(list_cluster_custom_object=broken)  # metrics-server not installed
    assert svc.list_nodes()[0].cpu_used == -1.0 and svc.list_pods()[0].mem_used == -1.0
    svc._custom = None
    assert svc.node_usage() == {} and svc.pod_usage("ns") == {}


def test_usage_reaches_pods_on_a_node_and_a_single_pod():
    def pod(name, node):
        return k.V1Pod(metadata=k.V1ObjectMeta(name=name, namespace="ns"),
                       spec=k.V1PodSpec(containers=[], node_name=node), status=k.V1PodStatus(phase="Running"))

    svc = KubeService()
    svc._core = NS(list_pod_for_all_namespaces=lambda **kw: NS(items=[pod("a", "n1"), pod("b", "n1")], metadata=None),
                   read_namespaced_pod=lambda name, ns, **kw: pod(name, "n1"))
    metric = lambda name: {"metadata": {"name": name, "namespace": "ns"},
                           "containers": [{"usage": {"cpu": "200m", "memory": "64Mi"}}]}
    svc._custom = NS(
        list_cluster_custom_object=lambda *a, **kw: {"items": [metric("a")]},  # b has no metrics yet
        get_namespaced_custom_object=lambda group, version, ns, plural, name, **kw: metric(name))
    a, b = svc.list_node_pods("n1")
    assert (a.cpu_used, a.mem_used) == (0.2, 64.0) and (b.cpu_used, b.mem_used) == (-1.0, -1.0)
    one = svc.read_pod("a", "ns")
    assert (one.cpu_used, one.mem_used) == (0.2, 64.0)

    def broken(*args, **kw):
        raise k.ApiException(status=404)

    svc._custom = NS(list_cluster_custom_object=broken, get_namespaced_custom_object=broken)
    assert svc.read_pod("a", "ns").cpu_used == -1.0 and svc.list_node_pods("n1")[0].mem_used == -1.0


def test_overview_usage_bars(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    view.set_data({"counts": {"nodes": 1}, "events": [], "lists": {}})
    assert not view.usage.isVisible()  # no metrics: no bars
    view.set_data({"counts": {"nodes": 1, "usage": {"cpu": (3.0, 4.0), "memory": (1.0, 8.0)}}, "events": [], "lists": {}})
    assert view.usage.isVisible() and "75%" in view.cpu_bar.text and "12%" in view.mem_bar.text
    assert abs(view.cpu_bar.fraction - 0.75) < 1e-9 and UsageBar("x").fraction == 0.0
    view.cpu_bar.grab()  # paints without error
    view.set_data({"counts": {"nodes": 1}, "events": [], "lists": {}})
    assert not view.usage.isVisible()


def test_unavailable_usage_columns_are_blank(qtbot):
    ws = ClusterWorkspace(None, "t")
    qtbot.addWidget(ws)
    ws.node_table.set_rows([NodeRow("a", "Ready", "w", "v", "ip", 4.0, 8.0, "1d", {}, 1.25, 2.5),
                            NodeRow("b", "Ready", "w", "v", "ip", 4.0, 8.0, "1d", {})])
    headers = [c[0] for c in ws.node_table.model.columns]
    cpu, mem = headers.index("CPU used"), headers.index("Mem used (GiB)")
    cells = [[ws.node_table.model.index(r, c).data() for c in (cpu, mem)] for r in (0, 1)]
    assert cells == [["1.25", "2.50"], ["", ""]]
