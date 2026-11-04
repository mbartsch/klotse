from PySide6.QtCore import QSettings

from klotse.plugins import Column, Plugin
from klotse.plugins.manager import Loaded, PluginManager
from klotse.ui.workspace import ClusterWorkspace
from test_plugins import Runner, node


class Long(Plugin):
    id = "long"
    name = "Long"
    columns = (Column("launched", "Launched"),)

    def enrich(self, request):
        return {r.key: {"launched": "2026-11-04 08:55 UTC and some more text to make the column wide"} for r in request.resources}


def workspace(qtbot, tmp_path):
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), folder=None, bundled=(), entry_points=False)
    manager.loaded = [Loaded("long", "bundled", "long", Long())]
    manager.set_enabled("long", True)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    ws.plugin_host._run = Runner()
    return ws


def column(ws):
    return [c[1] for c in ws.node_table.model.columns].index("plugin:long:launched")


def text(ws, row=0):
    return ws.node_table.model.index(row, column(ws)).data()


def test_a_refresh_keeps_the_values_and_the_column_width(qtbot, tmp_path):
    ws = workspace(qtbot, tmp_path)
    table = ws.node_table
    table.set_rows([node("n1"), node("n2")])
    assert text(ws) == ""  # the first time: nothing known yet
    empty_width = table.view.columnWidth(column(ws))
    ws.plugin_host._run.finish()
    full_width = table.view.columnWidth(column(ws))
    assert text(ws) and full_width > empty_width  # the values arrived and the column grew to them
    for _ in range(3):  # the 10 s refreshes: new row objects every time
        table.set_rows([node("n1"), node("n2")])
        assert text(ws) and text(ws, 1)  # the values are there at once, no blank flash
        assert table.view.columnWidth(column(ws)) == full_width  # and the width is not reset
    assert ws.plugin_host._run.calls == []  # nothing was asked again


def test_the_values_are_on_the_rows_before_the_table_is_measured(qtbot, tmp_path):
    ws = workspace(qtbot, tmp_path)
    table = ws.node_table
    table.set_rows([node("n1")])
    ws.plugin_host._run.finish()
    seen = []
    table.rows_incoming.connect(lambda rows: seen.append([dict(getattr(r, "extra", {})) for r in rows]))
    fresh = [node("n1")]
    assert not hasattr(fresh[0], "extra")  # a freshly loaded row knows nothing
    table.set_rows(fresh)
    # the workspace's own handler is connected first, so it has already put the known values on the row when the table
    # (which measures the columns right after announcing the rows) goes on
    assert seen[0][0]["plugin:long:launched"].startswith("2026-11-04")


def test_values_arriving_later_widen_the_plugin_columns_once(qtbot, tmp_path):
    ws = workspace(qtbot, tmp_path)
    table = ws.node_table
    table.set_rows([node("n1")])
    narrow = table.view.columnWidth(column(ws))
    table.view.setColumnWidth(0, 55)  # a width the user chose for another column
    ws.plugin_host._run.finish()
    assert table.view.columnWidth(column(ws)) > narrow
    assert table.view.columnWidth(0) == 55  # other columns are left alone
