"""Right-click 'Go to node ...' / 'Go to namespace ...': take the user to that node or namespace."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMenu

from klotse.k8s.models import NamespaceRow, NodeRow, PodRow
from klotse.ui.workspace import ClusterWorkspace


def node(name):
    return NodeRow(name, "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"metadata": {"name": name, "uid": f"u-{name}"}})


def pod(name="web-1", ns="shop", on="n2"):
    return PodRow(name, ns, "1/1", "Running", 0, on, "1d", {"metadata": {"name": name, "namespace": ns, "uid": "p"}})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def entries(table, row):
    menu = QMenu()
    table._goto_menu(menu, row)
    table._test_menu = menu  # keep the menu (and so its actions) alive for the test
    return {a.text(): a for a in menu.actions() if not a.isSeparator()}


# ---- the menu ----------------------------------------------------------------------------------------------------
def test_a_pod_offers_its_node_and_its_namespace(qtbot):
    ws = make_ws(qtbot)
    got = []
    ws.pod_table.goto_requested.connect(lambda what, name: got.append((what, name)))
    found = entries(ws.pod_table, pod())
    assert list(found) == ["Go to node n2", "Go to namespace shop"]
    found["Go to node n2"].trigger()
    found["Go to namespace shop"].trigger()
    assert got == [("node", "n2"), ("namespace", "shop")]


def test_a_pod_that_is_not_scheduled_has_no_node_entry(qtbot):
    ws = make_ws(qtbot)
    assert list(entries(ws.pod_table, pod(on=""))) == ["Go to namespace shop"]


def test_other_namespaced_objects_offer_their_namespace_only(qtbot):
    ws = make_ws(qtbot)
    for table in (ws.deploy_table, ws.rs_table, ws.sts_table):
        assert list(entries(table, SimpleNamespace(name="a", namespace="shop", node="n9"))) == ["Go to namespace shop"]


def test_namespaces_and_nodes_have_no_go_to_entries(qtbot):
    ws = make_ws(qtbot)
    assert entries(ws.ns_table, SimpleNamespace(name="shop", namespace="")) == {}
    assert entries(ws.node_table, node("n1")) == {}
    assert entries(ws.pod_table, SimpleNamespace(name="x", namespace="", node="")) == {}


def test_an_object_with_a_delete_handler_goes_to_the_namespace_without_deleting(qtbot):
    """The RBAC lists send their other row actions to delete; navigation has a signal of its own."""
    deleted = []
    ws = make_ws(qtbot, delete_resource=lambda *a: deleted.append(a))
    role_table = next(t for t in ws.resource_tables() if t.kind == "Role")
    went = []
    ws.go_to = lambda what, name: None  # the table's signal reaches it; what matters is what the signal carries
    role_table.goto_requested.connect(lambda what, name: went.append((what, name)))
    entries(role_table, SimpleNamespace(name="r", namespace="shop"))["Go to namespace shop"].trigger()
    assert went == [("namespace", "shop")] and deleted == []


# ---- going there -------------------------------------------------------------------------------------------------
def test_going_to_a_node_opens_the_nodes_list_with_that_node_selected(qtbot):
    shown = []
    ws = make_ws(qtbot, list_nodes=lambda: [node("n1"), node("n2"), node("n3")])
    ws.detail.show_row = lambda row: shown.append(row.name)
    ws.node_table.selected.disconnect()
    ws.node_table.selected.connect(lambda row: shown.append(row.name))
    ws.go_to("node", "n2")
    qtbot.waitUntil(lambda: ws.node_table.model.rowCount() == 3, timeout=5000)
    assert ws.foreground_key() == "nodes"
    qtbot.waitUntil(lambda: shown == ["n2"], timeout=5000)
    assert [r.name for r in ws.node_table.selected_rows()] == ["n2"]


def test_going_to_a_node_that_is_already_loaded_selects_it_at_once(qtbot):
    ws = make_ws(qtbot)
    ws.node_table.set_rows([node("n1"), node("n2")])
    ws.node_table.select_when_loaded("n1")
    assert [r.name for r in ws.node_table.selected_rows()] == ["n1"]


def test_a_node_that_is_not_there_selects_nothing_and_the_wish_is_dropped_after_the_load(qtbot):
    ws = make_ws(qtbot)
    ws.node_table.select_when_loaded("gone")
    ws.node_table.set_rows([node("n1")])
    assert ws.node_table.selected_rows() == []
    ws.node_table.set_rows([node("n1"), node("gone")])  # a later load must not suddenly jump
    assert ws.node_table.selected_rows() == []


def test_going_to_a_namespace_scopes_the_toolbar_and_opens_its_pods(qtbot):
    ws = make_ws(qtbot, list_pods=lambda ns=None: [])
    ws._set_namespaces([NamespaceRow("default", "Active", "1d", ""), NamespaceRow("shop", "Active", "1d", "")])
    ws.go_to("namespace", "shop")
    assert ws.namespace() == "shop" and ws.foreground_key() == "pods"


def test_an_unknown_namespace_says_so_and_does_not_move(qtbot):
    ws = make_ws(qtbot)
    said = []
    ws.message.connect(said.append)
    before = ws.namespace()
    ws.go_to("namespace", "nowhere")
    assert ws.namespace() == before and "nowhere" in said[0] and "not in the list" in said[0]


@pytest.mark.parametrize("what", ["", "pod", "cluster"])
def test_an_unknown_target_does_nothing(qtbot, what):
    ws = make_ws(qtbot)
    ws.go_to(what, "x")
    assert ws.foreground_key() != "nodes"
