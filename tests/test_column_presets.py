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

def test_preset_switch_keeps_selection_and_column_width(qtbot):
    from types import SimpleNamespace
    table = ResourceTable([("Name", "name"), ("Status", "status"), ("Version", "version")], "Node")
    qtbot.addWidget(table)
    table.set_rows([SimpleNamespace(name="n1", status="Ready", version="v1", raw={})])
    table.presets.setCurrentText("All")
    version = next(i for i, c in enumerate(table.model.columns) if c[1] == "version")
    table.view.setColumnWidth(version, 173)
    table.view.selectRow(0)
    table.presets.setCurrentText("Core")
    table.presets.setCurrentText("All")
    assert table.view.columnWidth(version) == 173
    assert table.selected_rows()[0].name == "n1"

def test_custom_layout_can_show_non_core_and_core_always_shows_status(qtbot):
    table = ResourceTable([("Name", "name"), ("Status", "status"), ("Version", "version")], "Node")
    qtbot.addWidget(table)
    table.set_label_columns([], {}, {"hidden": ["status"]})
    status = next(i for i, c in enumerate(table.model.columns) if c[1] == "status")
    version = next(i for i, c in enumerate(table.model.columns) if c[1] == "version")
    assert not table.view.isColumnHidden(status)
    table.presets.setCurrentText("Custom")
    assert table.view.isColumnHidden(status) and not table.view.isColumnHidden(version)
    table.presets.setCurrentText("Core")
    assert not table.view.isColumnHidden(status) and table.view.isColumnHidden(version)
    table.set_label_columns([], {}, {"hidden": []})
    assert table.presets.currentText() == "Custom"
    assert not table.view.isColumnHidden(version)


def test_all_and_provider_presets_override_custom_hidden_columns(qtbot):
    table = ResourceTable([("Name", "name"), ("Status", "status"), ("Version", "version")], "Node")
    qtbot.addWidget(table)
    table.set_extra_columns([("AWS ID", "plugin:aws:id")])
    table.set_plugin_presets({"AWS": ["plugin:aws:id"]})
    table.set_label_columns([], {}, {"hidden": ["version", "plugin:aws:id"]})
    def hidden(attr):
        index = next(i for i, column in enumerate(table.model.columns) if column[1] == attr)
        return table.view.isColumnHidden(index)
    table.presets.setCurrentText("Custom")
    assert hidden("version") and hidden("plugin:aws:id")
    table.presets.setCurrentText("All")
    assert not hidden("version") and not hidden("plugin:aws:id")
    table.presets.setCurrentText("AWS")
    assert not hidden("plugin:aws:id")
