from klotse.k8s.models import NamespaceRow
from klotse.ui.resource_table import ResourceTable


def table_for_test(qtbot):
    table = ResourceTable([("Name", "name"), ("Status", "status"), ("Age", "age")], "Namespace")
    qtbot.addWidget(table)
    return table


def test_refresh_failure_marks_retained_data_and_recovers(qtbot):
    table = table_for_test(qtbot)
    row = NamespaceRow("default", "Active", "1d", "", {})
    table.set_rows([row])
    table.view.selectRow(0)
    loaded = table._loaded_at
    table.set_loading()
    table.set_error("timeout\nmore detail")
    assert f"Refresh failed · showing data from {loaded}" in table.status.text()
    assert table.model.rows == [row]
    assert len(table.view.selectionModel().selectedRows()) == 1
    assert not table.retry_button.isHidden() and not table.error_details_button.isHidden()
    calls = []
    table.refresh_requested.connect(lambda: calls.append(True))
    table.retry_button.click()
    assert calls == [True]
    table.set_rows([row])
    assert "failed" not in table.status.text().lower()
    assert table.retry_button.isHidden() and table.error_details_button.isHidden()


def test_plugin_values_and_refresh_preserve_column_width(qtbot):
    table = table_for_test(qtbot)
    table.set_extra_columns([("Cloud", "plugin:cloud:value")])
    row = NamespaceRow("default", "Active", "1d", "", {})
    table.set_rows([row])
    index = next(i for i, col in enumerate(table.model.columns) if col[1] == "plugin:cloud:value")
    table.view.setColumnWidth(index, 145)
    row.extra = {"plugin:cloud:value": "long value " * 100}
    table.model.refresh_plugin_columns()
    table.set_rows([row])
    assert table.view.columnWidth(index) == 145
    table.fit_columns()
    assert table.view.columnWidth(index) <= 240
    assert table.model.data(table.model.index(0, index), 3) == "long value " * 100


def test_column_changes_keep_selection_and_width_after_plugin_is_disabled(qtbot):
    table = table_for_test(qtbot)
    plugin = ("Cloud", "plugin:cloud:value")
    table.set_extra_columns([plugin])
    rows = [NamespaceRow(f"node-{i}", "Active", "1d", "", {}) for i in range(40)]
    table.resize(320, 220)
    table.show()
    table.set_rows(rows)
    table.view.selectRow(20)
    table.view.scrollTo(table.proxy.index(20, 0))
    index = next(i for i, col in enumerate(table.model.columns) if col[1] == plugin[1])
    table.view.setColumnWidth(index, 173)
    selected = []
    table.selected.connect(selected.append)
    table.set_extra_columns([])
    assert table.view.currentIndex().isValid()
    assert table.proxy.index(table.view.currentIndex().row(), 0).data() == "node-20"
    assert len(table.view.selectionModel().selectedRows()) == 1
    assert not selected
    table.set_extra_columns([plugin])
    index = next(i for i, col in enumerate(table.model.columns) if col[1] == plugin[1])
    assert table.view.columnWidth(index) == 173
    assert table.proxy.index(table.view.currentIndex().row(), 0).data() == "node-20"
    assert not selected


def test_empty_first_load_waits_to_fit_columns_until_rows_arrive(qtbot):
    table = table_for_test(qtbot)
    table.set_rows([])
    assert not table._sized_columns
    table.set_rows([NamespaceRow("a-much-longer-resource-name-than-the-heading", "Active", "1d", "", {})])
    assert table._sized_columns
    assert table.view.columnWidth(0) > 100
