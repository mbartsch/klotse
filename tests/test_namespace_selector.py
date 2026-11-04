from PySide6.QtCore import Qt

from klotse.k8s.models import NamespaceRow
from klotse.ui.workspace import ALL_NS, ClusterWorkspace


def make_ws(qtbot):
    ws = ClusterWorkspace(None, "ctx")
    qtbot.addWidget(ws)
    ws.loads = []
    ws.load_view = lambda key: ws.loads.append((key, ws.namespace()))
    return ws


def rows(*names):
    return [NamespaceRow(name, "Active", "1d", "", {}) for name in names]


def test_a_new_cluster_starts_in_the_default_namespace(qtbot):
    ws = make_ws(qtbot)
    assert ws.namespace() == "default" and ws.ns_combo.currentText() == "default"
    ws._set_namespaces(rows("kube-system", "default", "team-a"))
    assert ws.namespace() == "default"
    assert [ws.ns_combo.itemText(i) for i in range(ws.ns_combo.count())] == [ALL_NS, "kube-system", "default", "team-a"]


def test_without_a_default_namespace_it_falls_back_to_all_and_reloads(qtbot):
    ws = make_ws(qtbot)
    ws.open_view("pods")
    ws.loads.clear()
    ws._set_namespaces(rows("team-a", "team-b"))
    assert ws.namespace() == ALL_NS and ws.ns_combo.currentText() == ALL_NS
    assert ("pods", ALL_NS) in ws.loads  # the view that loaded with "default" is reloaded for all namespaces


def test_a_chosen_namespace_survives_the_namespace_list_reloading(qtbot):
    ws = make_ws(qtbot)
    ws._set_namespaces(rows("default", "team-a"))
    ws.set_namespace("team-a")
    ws._set_namespaces(rows("default", "team-a", "team-b"))
    assert ws.namespace() == "team-a"
    ws._set_namespaces(rows("default", "team-b"))  # it was deleted
    assert ws.namespace() == ALL_NS


def test_the_selector_is_searchable(qtbot):
    ws = make_ws(qtbot)
    ws._set_namespaces(rows("default", "kube-system", "team-alpha", "team-beta", "monitoring"))
    combo = ws.ns_combo
    assert combo.isEditable()
    completer = combo.completer()
    completer.setCompletionPrefix("TEAM")  # case-insensitive
    names = [completer.completionModel().index(i, 0).data() for i in range(completer.completionModel().rowCount())]
    assert names == ["team-alpha", "team-beta"]
    completer.setCompletionPrefix("onito")  # matches anywhere in the name, not only the start
    assert completer.completionModel().rowCount() == 1


def test_a_half_typed_name_never_scopes_a_load(qtbot):
    ws = make_ws(qtbot)
    ws._set_namespaces(rows("default", "team-a"))
    ws.ns_combo.setEditText("tea")
    assert ws.namespace() == "default"  # still the chosen one
    ws.ns_combo.lineEdit().editingFinished.emit()  # Enter or focus lost without picking anything valid
    assert ws.ns_combo.currentText() == "default"


def test_picking_a_namespace_from_the_list_reloads_the_view(qtbot):
    ws = make_ws(qtbot)
    ws._set_namespaces(rows("default", "team-a"))
    ws.open_view("pods")
    ws.loads.clear()
    index = ws.ns_combo.findText("team-a", Qt.MatchExactly)
    ws.ns_combo.setCurrentIndex(index)
    ws.ns_combo.activated.emit(index)
    assert ws.namespace() == "team-a" and ws.loads[-1] == ("pods", "team-a")


def make_loading_ws(qtbot):
    """A workspace whose fetches stay in flight until the test completes them."""
    ws = ClusterWorkspace(None, "ctx")
    qtbot.addWidget(ws)
    started, shown = [], []
    ws._run = lambda fn, ok, *args, on_error=None: started.append((args[0], ok, on_error))
    ws.loaders["pods"] = (lambda ns: [], True, lambda data: shown.append(data))
    ws._set_namespaces(rows("default", "team-a"))
    return ws, started, shown


def test_changing_namespace_mid_load_abandons_it_and_loads_the_new_one_at_once(qtbot):
    ws, started, shown = make_loading_ws(qtbot)
    ws.load_view("pods")
    assert [s[0] for s in started] == ["default"]
    ws.set_namespace("team-a")
    ws.load_view("pods")  # what choosing a namespace does; starts at once, without waiting for "default"
    assert [s[0] for s in started] == ["default", "team-a"]
    started[0][1](["from default"])  # the abandoned request finally answers: ignored
    assert shown == [] and "pods" in ws._loading
    started[1][1](["from team-a"])
    assert shown == [["from team-a"]] and "pods" not in ws._loading


def test_an_abandoned_load_that_fails_does_not_show_an_error(qtbot):
    ws, started, shown = make_loading_ws(qtbot)
    ws.load_view("pods")
    ws.set_namespace("team-a")
    ws.load_view("pods")
    started[0][2]("ReadTimeoutError")
    assert "Load failed" not in ws.pod_table.status.text() and "pods" in ws._loading
    started[1][1]([])
    assert shown == [[]] and "pods" not in ws._loading


def test_switching_back_and_forth_only_keeps_the_newest_load(qtbot):
    ws, started, shown = make_loading_ws(qtbot)
    ws.load_view("pods")
    for name in ("team-a", "default"):
        ws.set_namespace(name)
        ws.load_view("pods")
    assert [s[0] for s in started] == ["default", "team-a", "default"]
    for _, ok, _ in started[:2]:
        ok(["stale"])
    assert shown == []
    started[2][1](["fresh"])
    assert shown == [["fresh"]]


def test_repeated_refreshes_of_the_same_namespace_are_still_coalesced(qtbot):
    ws, started, shown = make_loading_ws(qtbot)
    ws.load_view("pods")
    ws.load_view("pods")
    ws.load_view("pods")
    assert len(started) == 1
    started[0][1](["rows"])
    assert shown == [["rows"]] and len(started) == 2  # one follow-up load, not two
