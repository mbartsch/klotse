from klotse.ui.resource_table import ResourceTable


def test_saved_node_columns_are_applied_when_rows_load(qtbot):
    columns = [("Name", "name"), ("Status", "status"), ("Roles", "roles"), ("Version", "version"), ("Age", "age")]
    table = ResourceTable(columns, "Node", customizable=True)
    qtbot.addWidget(table)

    # This is the initial workspace customization pass, before plugin columns exist.
    table.set_label_columns([], {}, {"order": ["name", "status", "roles", "version", "age"], "hidden": ["version"]})
    table.set_rows([])

    assert table.presets.currentText() == "Custom"
    assert not table.view.isColumnHidden(0)
    assert not table.view.isColumnHidden(1)
    assert table.view.isColumnHidden(3)
    assert not table.view.isColumnHidden(4)
