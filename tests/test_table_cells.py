import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

from klotse.k8s.models import NodeRow, PodRow
from klotse.ui.resource_table import ResourceTable


def open_menu(table, monkeypatch, row=0, column=0, choose=None):
    actions = {}
    def show(menu, *_):
        actions.update({a.text(): a for a in menu.actions() if not a.isSeparator()})
        if choose:
            actions[choose].trigger()
    class Menu(QMenu):
        def exec(self, *args):
            return show(self, *args)
    monkeypatch.setattr("klotse.ui.resource_table.QMenu", Menu)
    table._context_menu(table.view.visualRect(table.proxy.index(row, column)).center())
    return actions


@pytest.mark.parametrize("kind", ["Node", "Pod"])
def test_copy_clicked_cell_after_sorting_and_with_multiple_rows_selected(qtbot, monkeypatch, kind):
    if kind == "Node":
        rows = [NodeRow(n, status, "worker", "v1", "", 1, 1, "1d", {})
                for n, status in (("a", "Ready"), ("z", "NotReady"))]
    else:
        rows = [PodRow(n, "ns", "1/1", status, 0, "node", "1d", {})
                for n, status in (("a", "Running"), ("z", "Pending"))]
    table = ResourceTable([("Name", "name"), ("Status", "status")], kind, editable=False)
    qtbot.addWidget(table)
    table.set_rows(rows)
    table.show()
    table.view.sortByColumn(0, Qt.DescendingOrder)
    table.view.selectAll()
    open_menu(table, monkeypatch, column=1, choose="Copy cell value")
    assert QApplication.clipboard().text() == rows[1].status
    assert len(table.selected_rows()) == 2
    open_menu(table, monkeypatch, column=1, choose="Filter by this value")
    assert table.filter.text() == f"status = '{rows[1].status}'"
    qtbot.waitUntil(lambda: not table._filter_timer.isActive())
    assert table.proxy.rowCount() == 1
    assert table.proxy.index(0, 0).data() == "z"
    assert not table.filter_note.isVisibleTo(table)
    table.filter.clear()
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 2)


@pytest.mark.parametrize("attr, value", [("status", 'a*? [x] "quoted" \'single\' = (one|two)'),
                                        ("annotation:example.org/note", 'line one\nline two "yes" \'ok\'' * 30),
                                        ("label:app.kubernetes.io/name", "web-*")])
def test_cell_filters_are_column_specific_and_keep_special_values_literal(qtbot, monkeypatch, attr, value):
    raw = {"metadata": {"annotations": {"example.org/note": value}, "labels": {"app.kubernetes.io/name": value}}}
    other_raw = {"metadata": {"annotations": {"example.org/note": "other"}, "labels": {"app.kubernetes.io/name": "other"}}}
    rows = [PodRow("selected", "ns", "1/1", value, 0, "n", "1d", raw),
            PodRow(value, "ns", "1/1", "other", 0, "n", "1d", other_raw)]
    table = ResourceTable([("Name", "name"), ("Value", attr)], "Pod", editable=False)
    qtbot.addWidget(table)
    table.set_rows(rows)
    table.show()
    # The common actions also work when a caller replaces the resource action menu.
    table.context_actions = lambda menu, rows: menu.addAction("Custom action")
    actions = open_menu(table, monkeypatch, column=1, choose="Copy cell value")
    assert "Custom action" in actions
    assert QApplication.clipboard().text() == value  # full annotation, not the shortened display
    open_menu(table, monkeypatch, column=1, choose="Filter by this value")
    qtbot.waitUntil(lambda: not table._filter_timer.isActive())
    assert table.proxy.rowCount() == 1
    assert table.proxy.index(0, 0).data() == "selected"


def test_formatted_numeric_cell_copies_display_and_filters_underlying_value(qtbot, monkeypatch):
    rows = [PodRow("a", "ns", "1/1", "Running", 0, "n", "1d", {}, cpu_used=0.1234),
            PodRow("b", "ns", "1/1", "Running", 0, "n", "1d", {}, cpu_used=0.12349)]
    table = ResourceTable([("Name", "name"), ("CPU (m)", "cpu_used", lambda v: f"{v * 1000:.0f}")], "Pod")
    qtbot.addWidget(table)
    table.set_rows(rows)
    table.show()
    open_menu(table, monkeypatch, column=1, choose="Copy cell value")
    assert QApplication.clipboard().text() == "123"
    open_menu(table, monkeypatch, column=1, choose="Filter by this value")
    qtbot.waitUntil(lambda: not table._filter_timer.isActive())
    assert table.proxy.rowCount() == 1 and table.proxy.index(0, 0).data() == "a"


def test_empty_cell_actions_do_not_clear_clipboard_or_filter(qtbot, monkeypatch):
    table = ResourceTable([("Name", "name"), ("Node", "node")], "Pod")
    qtbot.addWidget(table)
    table.set_rows([PodRow("a", "ns", "1/1", "Pending", 0, "", "1d", {})])
    table.show()
    QApplication.clipboard().setText("keep")
    actions = open_menu(table, monkeypatch, column=1)
    assert not actions["Copy cell value"].isEnabled()
    assert not actions["Filter by this value"].isEnabled()
    assert QApplication.clipboard().text() == "keep"


def test_copy_snapshot_survives_refresh_while_menu_is_open(qtbot, monkeypatch):
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    table.set_rows([PodRow("old", "ns", "1/1", "Running", 0, "n", "1d", {})])
    table.show()
    def show(menu, *_):
        table.set_rows([PodRow("new", "ns", "1/1", "Running", 0, "n", "1d", {})])
        next(a for a in menu.actions() if a.text() == "Copy cell value").trigger()
    class Menu(QMenu):
        def exec(self, *args):
            return show(self, *args)
    monkeypatch.setattr("klotse.ui.resource_table.QMenu", Menu)
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert QApplication.clipboard().text() == "old"


@pytest.mark.parametrize("value", ["my-pod", "10.0.0.1", "a [b] = (c|d)", "it's ready", 'say "ready"', r"path\to\file"])
def test_cell_filter_display_uses_readable_equality(qtbot, monkeypatch, value):
    table = ResourceTable([("Name", "name"), ("Status", "status")], "Pod")
    qtbot.addWidget(table)
    table.set_rows([PodRow("selected", "ns", "1/1", value, 0, "n", "1d", {}),
                    PodRow("other", "ns", "1/1", value + " extra", 0, "n", "1d", {})])
    table.show()
    open_menu(table, monkeypatch, column=1, choose="Filter by this value")
    quote = '"' if "'" in value else "'"
    assert table.filter.text() == f"status = {quote}{value}{quote}"
    qtbot.waitUntil(lambda: not table._filter_timer.isActive())
    assert table.proxy.rowCount() == 1
    assert table.proxy.index(0, 0).data() == "selected"
