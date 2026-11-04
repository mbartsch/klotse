from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFormLayout, QLineEdit, QPlainTextEdit

from klotse.plugins import Column, Plugin, Setting
from klotse.plugins.manager import Loaded, PluginManager
from klotse.ui.plugins_panel import PluginsPanel
from klotse.ui.workspace import ClusterWorkspace
from test_plugins import Runner, node


class Mapped(Plugin):
    id = "mapped"
    name = "Mapped"
    columns = (Column("v", "V"),)
    settings = (Setting("one", "One line", "x"), Setting("many", "Many lines", "a = 1", kind="multiline", help="one per line"))

    def __init__(self):
        self.seen = []

    def enrich(self, request):
        self.seen.append(request.settings["many"])
        return {r.key: {"v": request.settings["many"]} for r in request.resources}


def manager_for(tmp_path):
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), folder=None, bundled=(), entry_points=False)
    manager.loaded = [Loaded("mapped", "bundled", "mapped", Mapped())]
    manager.set_enabled("mapped", True)
    return manager


def boxes(panel):
    form = panel.settings_form
    return [form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget() for i in range(form.rowCount())]


def test_a_multiline_setting_is_a_text_box_that_saves_once_typing_stops(qtbot, tmp_path):
    manager = manager_for(tmp_path)
    panel = PluginsPanel(manager)
    qtbot.addWidget(panel)
    line, many = boxes(panel)
    assert isinstance(line, QLineEdit) and isinstance(many, QPlainTextEdit) and many.toPlainText() == "a = 1"
    assert many.toolTip() == "one per line"
    many.setPlainText("prod = p1\nstaging = p2")
    assert many.save_timer.isActive() and manager.settings_for("mapped")["many"] == "a = 1"  # not saved on every key
    many.save_timer.timeout.emit()
    assert manager.settings_for("mapped")["many"] == "prod = p1\nstaging = p2"
    again = PluginsPanel(PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), bundled=(), entry_points=False))
    assert again.manager.store.value("plugin_mapped/many") == "prod = p1\nstaging = p2"  # saved in the settings file


def test_typed_text_is_not_lost_when_the_page_is_left_before_the_timer_fires(qtbot, tmp_path):
    manager = manager_for(tmp_path)
    panel = PluginsPanel(manager)
    qtbot.addWidget(panel)
    panel.show()
    many = boxes(panel)[1]
    many.setPlainText("typed quickly")
    panel.hide()  # closing Preferences or switching tabs
    assert manager.settings_for("mapped")["many"] == "typed quickly" and not many.save_timer.isActive()
    panel.show()
    many = boxes(panel)[1]
    many.setPlainText("and again")
    panel.flush()
    assert manager.settings_for("mapped")["many"] == "and again"
    panel.flush()  # nothing pending now: no error, no change
    assert manager.settings_for("mapped")["many"] == "and again"


def test_a_setting_change_announces_which_plugin_and_comes_before_the_general_change(qtbot, tmp_path):
    manager = manager_for(tmp_path)
    order = []
    manager.settings_changed.connect(lambda plugin_id: order.append(("settings", plugin_id)))
    manager.changed.connect(lambda: order.append(("changed", None)))
    manager.set_setting("mapped", "one", "y")
    assert order == [("settings", "mapped"), ("changed", None)]
    order.clear()
    manager.set_enabled("mapped", False)
    assert order == [("changed", None)]  # enabling is not a setting change


def test_a_changed_setting_makes_the_open_lists_ask_the_plugin_again(qtbot, tmp_path):
    manager = manager_for(tmp_path)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.node_table.set_rows([node("n1")])
    run.finish()
    ws.node_table.set_rows([node("n1")])
    assert run.calls == []  # known: nothing to ask
    manager.set_setting("mapped", "many", "prod = other-profile")  # for example the AWS profile of the cluster
    assert len(run.calls) == 1  # what was fetched may be wrong now: asked again at once
    run.finish()
    plugin = manager.get("mapped").plugin
    assert plugin.seen == ["a = 1", "prod = other-profile"]
    col = [c[1] for c in ws.node_table.model.columns].index("plugin:mapped:v")
    assert ws.node_table.model.index(0, col).data() == "prod = other-profile"
