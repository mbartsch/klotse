"""Instances of well-known custom resources (Karpenter's NodePool and NodeClaim) get their own columns."""
from types import SimpleNamespace

import pytest

from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row, custom_columns, map_custom_object
from klotse.ui.workspace import ClusterWorkspace

NODEPOOL = {
    "metadata": {"name": "default", "creationTimestamp": "2020-01-01T00:00:00Z"},
    "spec": {"weight": 10, "limits": {"cpu": "1000"}, "disruption": {"consolidationPolicy": "WhenEmpty"},
             "template": {"spec": {"nodeClassRef": {"group": "karpenter.k8s.aws", "kind": "EC2NodeClass", "name": "al2"}}}},
    "status": {"resources": {"cpu": "48", "nodes": "6"}, "conditions": [{"type": "Ready", "status": "True"}]}}
NODECLAIM = {
    "metadata": {"name": "default-x7k2p", "labels": {"node.kubernetes.io/instance-type": "m5.large", "karpenter.sh/capacity-type": "spot",
                                                    "topology.kubernetes.io/zone": "eu-west-1a", "karpenter.sh/nodepool": "default"}},
    "status": {"nodeName": "ip-10-0-0-1", "conditions": [{"type": "Launched", "status": "True"}, {"type": "Initialized", "status": "False"}]}}


def test_a_nodepool_row_shows_its_class_limits_and_state():
    row = map_custom_object(NODEPOOL, group="karpenter.sh", kind="NodePool")
    assert (row.nodeclass, row.nodes, row.cpu, row.cpu_limit, row.weight, row.consolidation) == ("al2", "6", "48", "1000", "10", "WhenEmpty")
    assert row.ready == "✔ Ready" and row.name == "default" and row.age


def test_a_nodeclaim_row_shows_its_node_type_capacity_and_zone():
    row = map_custom_object(NODECLAIM, group="karpenter.sh", kind="NodeClaim")
    assert (row.node, row.instance_type, row.capacity, row.zone, row.nodepool) == ("ip-10-0-0-1", "m5.large", "spot", "eu-west-1a", "default")
    assert row.ready == "✖ Not initialized"  # the symbol and the words, not only a colour


@pytest.mark.parametrize("item", [{}, {"metadata": {}}, {"status": None, "spec": None}, {"status": {"conditions": "x"}},
                                  {"spec": {"template": None, "limits": 3}, "status": {"resources": []}}])
def test_missing_or_odd_fields_leave_cells_empty_and_never_raise(item):
    for kind in ("NodePool", "NodeClaim"):
        row = map_custom_object(item, group="karpenter.sh", kind=kind)
        assert row.name == "" and row.ready == ""


def test_other_kinds_keep_the_plain_row():
    row = map_custom_object({"metadata": {"name": "w"}}, group="example.com", kind="Widget")
    assert not hasattr(row, "nodeclass") and row.age == ""
    assert custom_columns("example.com", "Widget") is None
    assert [c[1] for c in custom_columns("karpenter.sh", "NodePool")][:3] == ["name", "nodeclass", "nodes"]


def test_the_service_hands_the_kind_to_the_mapper():
    svc = object.__new__(KubeService)
    svc._custom = SimpleNamespace(list_cluster_custom_object=lambda *a, **kw: {"items": [NODEPOOL], "metadata": {}})
    [row] = svc.list_custom_objects("karpenter.sh", "v1", "nodepools", False, None, "NodePool")
    assert row.nodeclass == "al2"
    [plain] = svc.list_custom_objects("karpenter.sh", "v1", "nodepools", False)
    assert not hasattr(plain, "nodeclass")


def crd(kind, plural, group, scope="Cluster"):
    return Row(f"{plural}.{group}", "", {}, group=group, kind=kind, scope=scope, plural=plural, version="v1", versions="v1", age="1d")


def test_browsing_a_nodepool_crd_opens_the_karpenter_columns(qtbot):
    asked = []
    service = SimpleNamespace(list_custom_objects=lambda *a: asked.append(a) or [map_custom_object(NODEPOOL, group="karpenter.sh", kind="NodePool")])
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: ws.loaders[key][2](ws.loaders[key][0](None))
    ws.open_custom_resources(crd("NodePool", "nodepools", "karpenter.sh"))
    table = ws.views["cr:nodepools.karpenter.sh"][0]
    assert [c[1] for c in table.base_columns] == ["name", "nodeclass", "nodes", "cpu", "cpu_limit", "weight", "consolidation", "ready", "age"]
    assert asked == [("karpenter.sh", "v1", "nodepools", False, None, "NodePool")]
    assert table.model.rowCount() == 1


def test_other_crds_still_get_name_namespace_and_age(qtbot):
    service = SimpleNamespace(list_custom_objects=lambda *a: [])
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    ws.open_custom_resources(crd("Widget", "widgets", "example.com", "Namespaced"))
    assert [c[1] for c in ws.views["cr:widgets.example.com"][0].base_columns] == ["name", "namespace", "age"]
