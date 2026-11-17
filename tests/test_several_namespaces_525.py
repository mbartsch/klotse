"""#525: several namespaces are picked in the namespace selector itself. A plain click selects one name; a tick adds or removes
one (the list stays open). Pods list the chosen namespaces together; other lists read the first one chosen."""
from types import SimpleNamespace as NS

from PySide6.QtCore import Qt

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ALL_NS, DEFAULT_NS, ClusterWorkspace


def pod(name, namespace):
    return PodRow(name, namespace, "1/1", "Running", 0, "n", "1d", {"metadata": {"name": name, "namespace": namespace, "uid": "u-" + name}})


def make_ws(qtbot, list_pods=None):
    ws = ClusterWorkspace(NS(list_pods=list_pods or (lambda ns, **o: [pod("web", ns)])), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    ws.ns_combo.addItems(["shop", "data"])
    ws._ns_names = [DEFAULT_NS, "shop", "data"]
    ws._fill_namespace_combo([DEFAULT_NS, "shop", "data"], DEFAULT_NS)
    ws._apply_picked(ALL_NS)  # start from All namespaces, so the first tick is the first name
    ws.load_view = lambda key: None
    ws._ready = True
    return ws


def capture_loads(ws):
    reads = []
    ws._run = lambda fn, done, *args, **kw: reads.append((fn, done, args))
    ws.load_view = ClusterWorkspace.load_view.__get__(ws)
    return reads


def test_a_tick_adds_a_name_and_the_first_chosen_is_the_selector(qtbot):
    ws = make_ws(qtbot)
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
    assert ws.namespaces() == ("shop", "data")
    assert ws.namespace() == "shop"
    assert ws.ns_combo.lineEdit().text() == "shop, data"


def test_a_tick_on_a_chosen_name_removes_it(qtbot):
    ws = make_ws(qtbot)
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
    ws._tick_namespace("shop")
    assert ws.namespaces() == ("data",)


def test_untick_everything_is_all_namespaces(qtbot):
    ws = make_ws(qtbot)
    ws._tick_namespace("shop")
    ws._tick_namespace("shop")  # the only name ticked off again
    assert ws.namespace() == ALL_NS and ws.namespaces() == (ALL_NS,)


def test_a_plain_choice_selects_only_that_name(qtbot):
    ws = make_ws(qtbot)
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
    ws.ns_combo.setCurrentIndex(ws.ns_combo.findText("shop"))
    ws._plain_namespace_chosen()
    assert ws.namespaces() == ("shop",)
    checked = {ws.ns_combo.itemText(r) for r in range(1, ws.ns_combo.count())
               if ws.ns_combo.model().item(r).checkState() == Qt.Checked}
    assert checked == {"shop"}


def test_all_namespaces_has_no_box_and_clears_the_ticks(qtbot):
    ws = make_ws(qtbot)
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
    ws._apply_picked(ALL_NS)
    assert ws.namespaces() == (ALL_NS,)
    assert ws.ns_combo.model().item(0).isCheckable() is False or ws.ns_combo.model().item(0).checkState() != Qt.Checked
    assert all(ws.ns_combo.model().item(r).checkState() != Qt.Checked for r in range(1, ws.ns_combo.count()))


def test_pods_read_every_chosen_namespace_and_other_lists_read_the_first(qtbot):
    ws = make_ws(qtbot)
    reads = capture_loads(ws)
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
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
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
    fn, done, args = [r for r in reads if r[2] == (("shop", "data"),)][-1]
    done(fn(*args))
    assert any("not read: data (" in text and "Forbidden" in text for text in seen), seen


def test_a_tick_zone_is_the_left_edge_of_a_row_only(qtbot):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt as Q
    from klotse.ui.workspace import _CheckZone
    ws = make_ws(qtbot)
    view = ws.ns_combo.view()
    view.show()
    zone = _CheckZone(ws)
    row = view.visualRect(ws.ns_combo.model().index(1, 0))
    def release(x):
        return QMouseEvent(QEvent.MouseButtonRelease, QPointF(x, row.center().y()), QPointF(x, row.center().y()),
                           Q.LeftButton, Q.LeftButton, Q.NoModifier)
    assert zone.eventFilter(view.viewport(), release(row.left() + 4)) is True  # the box toggles
    assert zone.eventFilter(view.viewport(), release(row.left() + 200)) is False  # the name is chosen as before
