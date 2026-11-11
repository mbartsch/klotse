from klotse.plugins.api import Column, Plugin
from klotse.plugins.manager import PluginManager
from klotse.ui.resource_table import ResourceTable


def test_node_presets_and_plugin_lifecycle(qtbot):
    table = ResourceTable([("Name", "name"), ("Status", "status"), ("Version", "version")], "Node")
    qtbot.addWidget(table)
    table.set_extra_columns([("AWS ID", "plugin:aws:id"), ("Zone", "plugin:azure:zone")])
    table.set_plugin_presets({"AWS": ["plugin:aws:id"], "Core": ["plugin:azure:zone"]})
    def hidden(attr):
        index = next(i for i, col in enumerate(table.model.columns) if col[1] == attr)
        return table.view.isColumnHidden(index)
    assert not hidden("name") and not hidden("status") and hidden("version")
    assert hidden("plugin:aws:id") and not hidden("plugin:azure:zone")
    table.presets.setCurrentText("AWS")
    assert not hidden("plugin:aws:id") and not hidden("plugin:azure:zone")
    table.presets.setCurrentText("All")
    assert not hidden("version") and not hidden("plugin:aws:id")
    table.presets.setCurrentText("AWS")
    table.set_extra_columns([("Zone", "plugin:azure:zone")])
    table.set_plugin_presets({"Core": ["plugin:azure:zone"]})
    assert table.presets.currentText() == "Core"
    assert table.presets.findText("AWS") == -1 and not hidden("plugin:azure:zone")


def test_plugin_preset_contributions():
    class Provider(Plugin):
        id = "aws"
        columns = (Column("id", "ID", presets=("AWS", "Core")),)
    # Use a tiny stand-in to exercise aggregation without Qt setup.
    manager = type("Plugins", (), {"active": lambda self: [Provider()]})()
    assert PluginManager.presets_for(manager, "Node") == {"AWS": ["plugin:aws:id"], "Core": ["plugin:aws:id"]}
