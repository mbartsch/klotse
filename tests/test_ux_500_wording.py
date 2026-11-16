"""#500: one wording per action, and the same order and marking in every menu (the remaining items after #526)."""
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from klotse.ui.overview import CLUSTER_CARDS, OverviewView
from klotse.ui.resource_table import ResourceTable
from klotse.ui.wording import counted


def _pod(name="web-1", node="", debug=True):
    spec = {"containers": [{"name": "app"}], "nodeName": node} if node else {"containers": [{"name": "app"}]}
    if debug:
        spec["ephemeralContainers"] = [{"name": "debugger-x"}]
    meta = {"name": name, "namespace": "shop", "ownerReferences": [{"kind": "ReplicaSet", "name": "web", "controller": True}]}
    return SimpleNamespace(name=name, namespace="shop", node=node, raw={"metadata": meta, "spec": spec})


def _popup_entries(qtbot, table, rows, rect_row=0):
    """The popup menu of a right-click on `rows`, flattened: submenu entries follow their parent, separators are None."""
    seen = []

    def flatten(menu):
        out = []
        for action in menu.actions():
            if action.isSeparator():
                out.append(None)
                continue
            out.append(action.text())
            if action.menu() is not None:
                out.extend(flatten(action.menu()))
        return out

    def read():
        menu = QApplication.activePopupWidget()
        seen.extend(flatten(menu))
        menu.close()

    table.set_rows(rows)
    table.view.selectRow(rect_row)
    QTimer.singleShot(200, read)
    table._context_menu(table.view.visualRect(table.proxy.index(rect_row, 0)).center())
    return seen


def test_capacity_has_one_name_in_the_overview_menu(qtbot):
    overview = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(overview)
    menu = overview.quick_actions_button.menu()
    labels = [a.text() for a in menu.actions()]
    assert "Capacity…" in labels and "Node capacity…" not in labels


def test_usage_history_of_several_pods_uses_the_same_noun_case_as_the_logs_entry(qtbot):
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    rows = [_pod("web-1"), _pod("web-2")]
    table.set_rows(rows)
    seen = []

    def read():
        menu = QApplication.activePopupWidget()
        seen.extend(a.text() for a in menu.actions())
        menu.close()

    QTimer.singleShot(200, read)
    table.view.selectRow(0)
    table.view.selectAll()
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert counted(2, "Pod").lower() == "2 pods"
    assert "Usage history of 2 pods…" in seen
    assert "Logs of 2 pods…" in seen


def test_remove_debug_containers_sits_with_the_destructive_entries(qtbot):
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    seen = _popup_entries(qtbot, table, [_pod()])
    remove = seen.index(next(t for t in seen if t and t.startswith("Remove debug containers")))
    force = seen.index("Force terminate…")
    logs = seen.index("Logs")
    assert logs < force < remove
    assert seen.index("Connect (shell)…") < force


def test_new_kind_entry_is_separated_from_the_go_to_entries(qtbot):
    table = ResourceTable([("Name", "name")], "Deployment")
    qtbot.addWidget(table)
    table.enable_new()
    deploy = SimpleNamespace(name="web", namespace="shop", raw={"metadata": {"name": "web", "namespace": "shop"}, "spec": {}})
    seen = _popup_entries(qtbot, table, [deploy])
    new_at = next(i for i, t in enumerate(seen) if t and t.startswith("New Deployment"))
    assert seen[new_at - 1] is None
    assert any(t and t.startswith("Go to namespace") for t in seen[:new_at])


def test_a_pod_menu_marks_entries_the_user_may_not_do(qtbot):
    # #500 part 17: "(not allowed for you)" on the single-row entries that were missing it
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    table.permission_check = lambda kind, action, row=None: False
    seen = _popup_entries(qtbot, table, [_pod()])
    assert any(t and t.startswith("Copy files…") and "not allowed" in t for t in seen)
    assert any(t and t.startswith("Port forward…") and "not allowed" in t for t in seen)
    assert any(t and t.startswith("Connect (shell)") and "not allowed" in t for t in seen)


def test_a_bulk_menu_marks_entries_the_user_may_not_do(qtbot):
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    table.permission_check = lambda kind, action, row=None: False
    table.set_rows([_pod("web-1"), _pod("web-2")])
    seen = []

    def read():
        menu = QApplication.activePopupWidget()
        seen.extend(a.text() for a in menu.actions())
        menu.close()

    QTimer.singleShot(200, read)
    table.view.selectAll()
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert any(t and t.startswith("Terminate 2 pods") and t.endswith("(not allowed for you)") for t in seen)


def test_duration_and_ratio_columns_sort_by_value_not_as_text():
    # #500 part 17: "10d" sorted before "2d" as text; ratios like "1/2" were text too
    from PySide6.QtCore import Qt
    from klotse.ui.table_model import TableModel, age_sort_value, ratio_sort_value
    model = TableModel([("Last Seen", "last_seen"), ("Ready", "ready")])
    rows = [SimpleNamespace(last_seen="10d", ready="2/2"), SimpleNamespace(last_seen="2d", ready="1/3")]
    model.set_rows(rows) if hasattr(model, "set_rows") else setattr(model, "rows", rows)
    seen = [model.index(i, 0).data(Qt.EditRole) for i in range(2)]
    assert seen == [age_sort_value("10d"), age_sort_value("2d")] and seen[0] > seen[1]
    ready = [model.index(i, 1).data(Qt.EditRole) for i in range(2)]
    assert ready[0] > ready[1]
    assert age_sort_value("1d2h") == 93600 and age_sort_value("nope") == -1 and ratio_sort_value("Ready") == -1


def test_a_namespace_label_error_shows_the_line_without_python_quotes():
    # #500 part 18: the error showed the line as a Python repr ('oops'), not as the user typed it
    import pytest
    from klotse.ui.namespace_dialog import parse_pairs
    with pytest.raises(ValueError) as error:
        parse_pairs("oops")
    assert str(error.value) == "Expected key=value, got: oops"


def test_auto_refresh_wording_is_one_family_in_view_and_navigator(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService, open_active
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    open_active(win)
    texts = [a.text() for a in win.auto_refresh_menu.actions()]
    assert "Turn auto-refresh off for all clusters" in texts and "Turn auto-refresh on for all clusters" in texts
    ctx = win.clusters.currentWidget().context
    menu = win.nav.menu_for(win.nav._clusters[ctx])
    labels = [a.text() for a in menu.actions() if a.text().startswith(("Turn", "Disable", "Enable"))]
    assert labels == ["Turn off auto-refresh"]  # the cluster is running with an interval: only the change that applies


def test_plugin_entries_come_last_for_one_row_and_for_several(qtbot):
    # #500 part 19: plugin entries were first for several rows and before the destructive group for one row
    from types import SimpleNamespace as NS
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    action = NS(title="Scan image", key="scan", dangerous=False)
    table.plugin_actions = lambda kind, rows: [("Scanner", "scanner", [action])]
    one = _popup_entries(qtbot, table, [_pod()])
    assert one.index("Scan image") > one.index("Force terminate…")
    table.set_rows([_pod("web-1"), _pod("web-2")])
    seen = []

    def read():
        menu = QApplication.activePopupWidget()
        seen.extend(a.text() for a in menu.actions())
        menu.close()

    QTimer.singleShot(200, read)
    table.view.selectAll()
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert seen.index("Scan image") > seen.index("Force terminate 2 pods…")


def test_the_pod_bar_offers_the_same_entries_as_the_pod_row(qtbot):
    from klotse.ui.pod_view import PodActionBar
    bar = PodActionBar()
    qtbot.addWidget(bar)
    texts = [a.text() for a in bar.items]
    for label in ("Port forward…", "Browse files…", "Debug (ephemeral container)…", "Copy as new…"):
        assert label in texts


def test_destructive_bar_entries_carry_the_trash_mark(qtbot):
    from klotse.ui.action_bar import actions_button
    button, items = actions_button([("Terminate…", "terminate"), ("Edit…", "edit")], lambda name: None)
    qtbot.addWidget(button)
    assert not items[0].icon().isNull() and items[1].icon().isNull()
