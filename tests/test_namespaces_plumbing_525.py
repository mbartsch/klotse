"""#525 step 1: every list reads its namespaces through namespaces(); the selector is still single-choice, so behaviour is unchanged."""
from types import SimpleNamespace as NS

from klotse.ui.workspace import ALL_NS, DEFAULT_NS, ClusterWorkspace


def make_ws(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    ws.show()
    ws._ready = True
    return ws


def test_namespaces_is_the_chosen_namespace_as_a_tuple(qtbot):
    ws = make_ws(qtbot)
    assert ws.namespace() == DEFAULT_NS and ws.namespaces() == (DEFAULT_NS,)


def test_all_namespaces_is_the_single_all_marker(qtbot):
    ws = make_ws(qtbot)
    ws.ns_combo.setCurrentIndex(ws.ns_combo.findText(ALL_NS))
    assert ws.namespaces() == (ALL_NS,)


def test_a_scoped_list_reads_the_chosen_namespace_and_all_reads_every_namespace(qtbot):
    ws = make_ws(qtbot)
    reads = []
    ws._run = lambda fn, done, *args, **kw: reads.append(args)
    ws.load_view = ClusterWorkspace.load_view.__get__(ws)
    ws.load_view("pods")
    assert reads[-1] == (DEFAULT_NS,)
    ws.ns_combo.setCurrentIndex(ws.ns_combo.findText(ALL_NS))
    ws.load_view("pods")
    assert reads[-1] == (None,)
