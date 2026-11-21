"""#821: Details is told about the selection whenever it becomes exactly one row, even when Qt's current row does not move:
Ctrl+click a row off and click it again, or Ctrl+click one of two selected rows off."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from klotse.k8s.resources import Row
from klotse.ui.resource_table import ResourceTable

COLUMNS = [("Name", "name"), ("Namespace", "namespace"), ("Status", "status")]


def table(qtbot, names):
    t = ResourceTable(COLUMNS, "Pod")
    qtbot.addWidget(t)
    t.resize(600, 300)
    t.show()
    t.set_rows([Row(n, "default", {"kind": "Pod", "metadata": {"name": n, "uid": "u-" + n}}, status="Running")
                for n in names])
    qtbot.waitExposed(t)
    shown = []
    t.selected.connect(shown.append)
    return t, shown


def click(t, name, modifier=Qt.NoModifier):
    proxy_row = next(r for r in range(t.proxy.rowCount())
                     if t.model.rows[t.proxy.mapToSource(t.proxy.index(r, 0)).row()].name == name)
    rect = t.view.visualRect(t.proxy.index(proxy_row, 0))
    QTest.mouseClick(t.view.viewport(), Qt.LeftButton, modifier, rect.center())


def last_name(shown):
    return shown[-1].name if shown[-1] is not None else None


def test_clicking_the_row_again_after_ctrl_click_shows_it(qtbot):
    t, shown = table(qtbot, ["web-1", "web-2"])
    click(t, "web-1")
    assert last_name(shown) == "web-1"
    click(t, "web-1", Qt.ControlModifier)
    assert not t.selected_rows() and shown[-1] is None
    click(t, "web-1")
    assert [r.name for r in t.selected_rows()] == ["web-1"]
    assert last_name(shown) == "web-1"


def test_ctrl_click_off_one_of_two_shows_the_one_left(qtbot):
    t, shown = table(qtbot, ["web-1", "web-2"])
    click(t, "web-1")
    click(t, "web-2", Qt.ControlModifier)
    assert len(t.selected_rows()) == 2
    click(t, "web-2", Qt.ControlModifier)
    assert [r.name for r in t.selected_rows()] == ["web-1"]
    assert last_name(shown) == "web-1"


def test_a_plain_click_tells_details_once(qtbot):
    t, shown = table(qtbot, ["web-1", "web-2"])
    click(t, "web-2")
    assert [r.name if r else None for r in shown] == ["web-2"]


def test_a_reload_that_keeps_the_selection_stays_silent(qtbot):
    t, shown = table(qtbot, ["web-1", "web-2"])
    click(t, "web-1")
    count = len(shown)
    t.set_rows([Row(n, "default", {"kind": "Pod", "metadata": {"name": n, "uid": "u-" + n}}, status="Running")
                for n in ["web-1", "web-2"]])
    assert len(shown) == count
