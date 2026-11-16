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
