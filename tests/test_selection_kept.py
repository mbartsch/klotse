"""A reload (auto-refresh every 10 s, or a watch event) replaces every row. The selection, the current row and the scroll
position must come back, silently, or the user loses their place while reviewing a list."""
from PySide6.QtWidgets import QAbstractItemView

from klotse.k8s.resources import Row
from klotse.ui.resource_table import ResourceTable

COLUMNS = [("Name", "name"), ("Namespace", "namespace"), ("Status", "status")]


def rows(names, uids=True):
    return [Row(n, "default", {"kind": "Role", "metadata": {"name": n, **({"uid": "u-" + n} if uids else {})}},
                status="Running") for n in names]


def table(qtbot, names):
    t = ResourceTable(COLUMNS, "Role")
    qtbot.addWidget(t)
    t.resize(600, 300)
    t.show()
    t.set_rows(rows(names))
    return t


def selected_names(t):
    return sorted(r.name for r in t.selected_rows())


def test_the_selection_survives_a_reload(qtbot):
    t = table(qtbot, [f"role-{i}" for i in range(8)])
    t.view.selectRow(3)
    t.set_rows(rows([f"role-{i}" for i in range(8)]))  # what an auto-refresh does
    assert selected_names(t) == ["role-3"]


def test_it_follows_the_object_when_the_order_changes(qtbot):
    t = table(qtbot, ["a", "b", "c", "d"])
    t.view.selectRow(1)  # b
    t.set_rows(rows(["d", "c", "b", "a"]))
    assert selected_names(t) == ["b"]
    assert t.proxy.mapToSource(t.view.currentIndex()).row() == 2


def test_several_selected_rows_are_all_kept_and_a_deleted_one_is_dropped(qtbot):
    t = table(qtbot, ["a", "b", "c", "d"])
    t.view.selectAll()
    t.set_rows(rows(["a", "c", "d", "e"]))  # b is gone, e is new
    assert selected_names(t) == ["a", "c", "d"]


def test_rows_without_a_uid_are_matched_by_namespace_and_name(qtbot):
    t = ResourceTable(COLUMNS, "Role")
    qtbot.addWidget(t)
    t.set_rows(rows(["a", "b"], uids=False))
    t.view.selectRow(1)
    t.set_rows(rows(["a", "b"], uids=False))
    assert selected_names(t) == ["b"]


def test_a_replaced_object_with_the_same_name_is_not_the_same_selection(qtbot):
    t = table(qtbot, ["a", "b"])
    t.view.selectRow(1)
    fresh = rows(["a", "b"])
    fresh[1].raw["metadata"]["uid"] = "someone-else"  # deleted and recreated
    t.set_rows(fresh)
    assert selected_names(t) == []


def test_restoring_is_silent_so_details_is_not_redrawn(qtbot):
    t = table(qtbot, ["a", "b", "c"])
    t.view.selectRow(1)
    seen = []
    t.selected.connect(seen.append)
    t.set_rows(rows(["a", "b", "c"]))
    assert selected_names(t) == ["b"] and seen == []  # a redraw would clear decoded secret keys


def test_a_row_hidden_by_the_filter_is_not_forced_back(qtbot):
    t = table(qtbot, ["alpha", "beta"])
    t.view.selectRow(1)
    t.filter.setText("alpha")
    qtbot.waitUntil(lambda: t.proxy.rowCount() == 1, timeout=3000)
    t.set_rows(rows(["alpha", "beta"]))
    assert selected_names(t) == []  # beta is not visible: nothing to select, and no error


def test_the_scroll_position_is_kept(qtbot):
    names = [f"role-{i:03d}" for i in range(200)]
    t = table(qtbot, names)
    t.view.selectRow(150)
    t.view.scrollTo(t.view.currentIndex(), QAbstractItemView.PositionAtCenter)
    before = t.view.verticalScrollBar().value()
    assert before > 0
    t.set_rows(rows(names))
    assert t.view.verticalScrollBar().value() == before and selected_names(t) == ["role-150"]


def test_first_load_and_empty_tables_are_untouched(qtbot):
    t = ResourceTable(COLUMNS, "Role")
    qtbot.addWidget(t)
    t.set_rows([])
    t.set_rows(rows(["a"]))
    assert selected_names(t) == []


def test_a_real_refresh_through_the_workspace_keeps_the_selection(qtbot):
    from klotse.k8s.resources import SPEC_BY_KEY
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService, open_active

    cols = {attr: "" for _, attr in SPEC_BY_KEY["roles"].columns if attr not in ("name", "namespace")}
    loads = []

    class Svc(FakeService):
        def list_resource(self, key, namespace=None):
            loads.append(key)
            return [Row(f"role-{i}", "default", {"kind": "Role", "metadata": {"name": f"role-{i}", "uid": f"u{i}"}},
                        **cols) for i in range(6)]

    win = MainWindow(Svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("roles")
    tbl = next(t for t in ws.resource_tables() if t.kind == "Role")
    qtbot.waitUntil(lambda: tbl.model.rowCount() == 6, timeout=5000)
    tbl.view.selectRow(4)
    first = len(loads)
    ws.load_view("roles")  # the same call the 10 s auto-refresh and the Refresh button make
    qtbot.waitUntil(lambda: len(loads) > first, timeout=5000)
    qtbot.wait(300)
    assert selected_names(tbl) == ["role-4"]
