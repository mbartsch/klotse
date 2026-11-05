"""The "pods on this node" tab carries the node actions (cordon, drain, delete…)."""
from types import SimpleNamespace as NS

from klotse.k8s.models import NodeRow
from klotse.ui.workspace import ClusterWorkspace


def node(name, cordoned=False):
    spec = {"unschedulable": True} if cordoned else {}
    return NodeRow(name, "Ready,SchedulingDisabled" if cordoned else "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d",
                   {"metadata": {"uid": "u-" + name}, "spec": spec})


def make_ws(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    ws.show()
    ws._ready = True
    return ws


def texts(bar):
    return [b.text() for b in bar.items]


def test_pods_tab_has_node_action_bar(qtbot):
    ws = make_ws(qtbot)
    row = node("n1")
    ws.open_node_pods("n1", row)
    table = ws.views["node-pods:n1"][0]
    bar = ws._node_bars["n1"][0]
    assert bar.parent() is table and bar.title.text() == "Node n1"
    assert texts(bar) == ["Cordon", "Drain…", "Shell (nsenter)…", "Delete node…", "Force delete…"]
    assert "Ready" in bar.state.text()


def test_buttons_trigger_node_actions_on_the_current_node_row(qtbot):
    ws = make_ws(qtbot)
    done = []
    ws.node_action = lambda action, row: done.append((action, row.name, row.raw["metadata"]["uid"]))
    ws.open_node_pods("n1", node("n1"))
    bar = ws._node_bars["n1"][0]
    for button in bar.items:
        button.trigger()
    assert done == [("cordon", "n1", "u-n1"), ("drain", "n1", "u-n1"), ("node-shell", "n1", "u-n1"),
                    ("delete-node", "n1", "u-n1"), ("force-delete-node", "n1", "u-n1")]


def test_cordon_button_follows_node_state_and_nodes_reload(qtbot):
    ws = make_ws(qtbot)
    done = []
    ws.node_action = lambda action, row: done.append(action)
    ws.open_node_pods("n1", node("n1"))
    bar = ws._node_bars["n1"][0]
    ws._set_nodes([node("n1", cordoned=True)])  # the Nodes list reloaded and the node is now cordoned
    assert bar.cordon.text() == "Uncordon" and "SchedulingDisabled" in bar.state.text()
    bar.cordon.trigger()
    assert done == ["uncordon"]
    ws._set_nodes([node("n1")])
    assert bar.cordon.text() == "Cordon"
    ws._set_nodes([node("other")])  # node deleted
    assert not any(b.isEnabled() for b in bar.items) and "no longer" in bar.state.text()
    ws.open_node_pods("n1")  # reopening the tab keeps it consistent with the list
    assert not any(b.isEnabled() for b in bar.items) or bar.state.text() == ""


def test_action_without_loaded_node_row_uses_row_it_was_opened_from(qtbot):
    ws = make_ws(qtbot)
    done, errors = [], []
    ws.node_action = lambda action, row: done.append(row.name)
    ws._on_error = errors.append
    ws.open_node_pods("n1", node("n1"))
    ws._node_bar_action("drain", "n1")  # Nodes list was never loaded: falls back to the opening row
    assert done == ["n1"] and errors == []
    ws.open_node_pods("n2")  # opened without a row and with no node list
    ws._node_bar_action("drain", "n2")
    assert done == ["n1"] and errors and "n2" in errors[0]


def test_node_change_reloads_the_nodes_list_even_in_background(qtbot):
    ws = make_ws(qtbot)
    loads = []
    ws.load_view = loads.append
    ws.open_node_pods("n1", node("n1"))
    loads.clear()
    ws._node_changed("n1", "Cordon completed")
    assert "nodes" in loads  # so the bar's Cordon/Uncordon label and the "gone" state stay current


def test_double_click_node_opens_its_pods_tab_like_pod_opens_containers(qtbot):
    ws = make_ws(qtbot)
    ws._set_nodes([node("n1"), node("n2")])
    ws.node_table._on_double_click(ws.node_table.proxy.index(1, 0))
    assert ws.foreground_key() == "node-pods:n2" and "n2" in ws._node_bars
    assert ws._node_bars["n2"][0].title.text() == "Node n2"
    opened = []
    ws.pod_action = lambda action, row: opened.append((action, row.name))
    from klotse.k8s.models import PodRow

    ws.pod_table.set_rows([PodRow("web", "ns", "1/1", "Running", 0, "n2", "1d", {})])
    ws.pod_table.action_requested.disconnect()
    ws.pod_table.action_requested.connect(ws.pod_action)
    ws.pod_table._on_double_click(ws.pod_table.proxy.index(0, 0))
    assert opened == [("containers", "web")]  # pods keep their own double-click
