"""#525 step 2b: choosing several namespaces. Pods read them all; other lists read the first one; picking one in the selector ends it."""
from types import SimpleNamespace as NS

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ALL_NS, DEFAULT_NS, ClusterWorkspace


def pod(name, namespace):
    return PodRow(name, namespace, "1/1", "Running", 0, "n", "1d", {"metadata": {"name": name, "namespace": namespace, "uid": "u-" + name}})


def make_ws(qtbot, list_pods=None):
    ws = ClusterWorkspace(NS(list_pods=list_pods or (lambda ns, **o: [pod("web", ns)])), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    ws.ns_combo.addItems(["shop", "data"])
    ws.load_view = lambda key: None
    ws._ready = True
    return ws


def capture_loads(ws):
    reads = []
    ws._run = lambda fn, done, *args, **kw: reads.append((fn, done, args))
    ws.load_view = ClusterWorkspace.load_view.__get__(ws)
    return reads


def test_several_namespaces_are_the_chosen_tuple_and_the_first_is_the_selector(qtbot):
    ws = make_ws(qtbot)
    ws.set_chosen_namespaces(["shop", "data"])
    assert ws.namespaces() == ("shop", "data")
    assert ws.namespace() == "shop"


def test_pods_read_every_chosen_namespace_and_other_lists_read_the_first(qtbot):
    ws = make_ws(qtbot)
    reads = capture_loads(ws)
    ws.set_chosen_namespaces(["shop", "data"])
    pods = [r for r in reads if r[2] == (("shop", "data"),)]
    assert pods, "the Pods load should receive the chosen tuple"
    fn, done, args = pods[-1]
    done(fn(*args))
    assert [(r.namespace, r.name) for r in ws.pod_table.model.rows] == [("shop", "web"), ("data", "web")]

    ws.load_view("deployments")
    assert reads[-1][2] == ("shop",)


def test_a_refused_namespace_is_named_in_the_status_line(qtbot):
    def list_pods(namespace, **options):
        if namespace == "data":
            raise RuntimeError('(403) Reason: Forbidden: pods is forbidden: User "u" cannot list resource "pods" in namespace "data"')
        return [pod("web", namespace)]
    ws = make_ws(qtbot, list_pods=list_pods)
    reads = capture_loads(ws)
    seen = []
    ws.message.connect(seen.append)
    ws.set_chosen_namespaces(["shop", "data"])
    fn, done, args = [r for r in reads if r[2] == (("shop", "data"),)][-1]
    done(fn(*args))
    assert any("not read: data (" in text and "Forbidden" in text for text in seen), seen


def test_choosing_several_from_the_checklist_sets_them(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws._ns_names = [ALL_NS, DEFAULT_NS, "shop", "data"]
    asked = []
    monkeypatch.setattr(ws, "_pick_namespaces", lambda names, chosen: asked.append((names, chosen)) or ["data", "shop"])
    ws.choose_several_namespaces()
    assert asked and DEFAULT_NS in asked[0][0] and ALL_NS not in asked[0][0]
    assert ws.namespaces() == ("data", "shop")


def test_a_cancelled_checklist_changes_nothing(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws._ns_names = [DEFAULT_NS, "shop"]
    monkeypatch.setattr(ws, "_pick_namespaces", lambda names, chosen: None)
    ws.choose_several_namespaces()
    assert ws.namespaces() == (DEFAULT_NS,)


def test_picking_one_namespace_in_the_selector_ends_the_several_choice(qtbot):
    ws = make_ws(qtbot)
    ws.set_chosen_namespaces(["shop", "data"])
    ws.ns_combo.setCurrentIndex(ws.ns_combo.findText(DEFAULT_NS))
    assert ws.namespaces() == (DEFAULT_NS,)
