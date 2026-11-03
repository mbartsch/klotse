from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog

from kubeman.ui.customizations import DEFAULT_LABELS, LabelColumnsDialog, LabelSettings
from kubeman.ui.main_window import MainWindow
from kubeman.ui.resource_table import ResourceTable

from test_app import FakeService


def preferences(tmp_path):
    return LabelSettings(QSettings(str(tmp_path / "kubeman.ini"), QSettings.IniFormat))


def test_preferences_persist_separate_ordered_label_lists(tmp_path):
    config = preferences(tmp_path)
    assert config.labels("Node") == ["karpenter.sh/nodepool"]
    assert config.labels("Pod") == []
    config.set_labels("Node", [" karpenter.sh/nodepool ", "example.com/team", "example.com/team", ""])
    config.set_labels("Pod", ["app.kubernetes.io/name", "team"])
    loaded = preferences(tmp_path)
    assert loaded.labels("Node") == ["karpenter.sh/nodepool", "example.com/team"]
    assert loaded.labels("Pod") == ["app.kubernetes.io/name", "team"]
    loaded.set_labels("Node", [])
    assert preferences(tmp_path).labels("Node") == []
    assert preferences(tmp_path).labels("Pod") == ["app.kubernetes.io/name", "team"]


@pytest.mark.parametrize("saved", ['not json', '[]', '{"Node": "wrong"}', '{"Node": ["team=value"]}'])
def test_invalid_saved_preferences_fall_back_to_defaults(tmp_path, saved):
    store = QSettings(str(tmp_path / "kubeman.ini"), QSettings.IniFormat)
    store.setValue("columns/labels", saved)
    assert LabelSettings(store).labels("Node") == DEFAULT_LABELS["Node"]


def test_failed_save_does_not_change_active_labels():
    nothing = lambda *a: None  # noqa: E731
    store = SimpleNamespace(value=lambda *a: "{}", setValue=nothing, remove=nothing, childGroups=lambda: [],
                            beginGroup=nothing, endGroup=nothing, sync=nothing,
                            status=lambda: QSettings.AccessError, fileName=lambda: "read-only.ini")
    config = LabelSettings(store)
    with pytest.raises(OSError, match="read-only.ini"):
        config.set_labels("Pod", ["app"])
    assert config.labels("Pod") == []


def test_dialog_add_save_and_cancel(qtbot, tmp_path):
    config = preferences(tmp_path)
    dialog = LabelColumnsDialog("Pod", config, ["team", "app"])
    qtbot.addWidget(dialog)
    dialog.available.setCurrentText("team")
    dialog._add()
    dialog._add()
    dialog.available.setCurrentText("app")
    dialog._add()
    assert dialog.editor.toPlainText() == "team\napp"
    dialog._save()
    assert dialog.result() == QDialog.Accepted
    assert preferences(tmp_path).labels("Pod") == ["team", "app"]
    cancelled = LabelColumnsDialog("Pod", config)
    qtbot.addWidget(cancelled)
    cancelled.editor.setPlainText("different")
    cancelled.reject()
    assert preferences(tmp_path).labels("Pod") == ["team", "app"]


def test_label_columns_sort_filter_and_restore_age_sort(qtbot):
    columns = [("Name", "name"), ("Status", "status"), ("Roles", "roles"), ("Age", "age")]
    table = ResourceTable(columns, "Node")
    qtbot.addWidget(table)
    rows = [SimpleNamespace(name="one", status="Ready", roles="worker", age="8h", raw={"metadata": {
                "labels": {"karpenter.sh/nodepool": "pool-b", "team": "payments"}}}),
            SimpleNamespace(name="two", status="Ready", roles="worker", age="1d", raw={"metadata": {"labels": {}}})]
    table.set_rows(rows)
    table.view.sortByColumn(3, Qt.DescendingOrder)
    table.set_label_columns(["karpenter.sh/nodepool", "team"])
    assert [c[0] for c in table.model.columns] == ["Name", "Status", "Roles", "NodePool", "team", "Age"]
    assert table.model.headerData(3, Qt.Horizontal, Qt.ToolTipRole) == "karpenter.sh/nodepool"
    assert table.proxy.index(0, 0).data() == "two"  # Age sort follows its column
    assert table.model.index(0, 3).data() == "pool-b"
    assert table.model.index(1, 3).data() == ""
    table.proxy.sort(3, Qt.DescendingOrder)
    assert table.proxy.index(0, 0).data() == "one"
    table.filter.setText("payments")
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 1)
    assert table.proxy.index(0, 0).data() == "one"
    table.set_label_columns([])
    assert table.model.rows == rows
    assert table.model.columns == columns


def test_live_settings_update_all_workspaces_and_node_pod_tabs(qtbot, tmp_path):
    config = preferences(tmp_path)
    service = FakeService()
    service.list_node_pods = lambda node: service.list_pods()
    window = MainWindow(lambda: service, label_settings=config)
    qtbot.addWidget(window)
    window.show()
    workspace = window.workspace("ctx")
    assert workspace.node_table.customize_button is not None and workspace.pod_table.customize_button is not None
    qtbot.waitUntil(lambda: workspace._opened_default and not workspace._loading)
    node_columns = workspace.node_table.model.columns
    roles = next(i for i, c in enumerate(node_columns) if c[1] == "roles")
    assert node_columns[roles + 1] == ("NodePool", "label:karpenter.sh/nodepool")
    config.set_labels("Pod", ["app", "team"])
    for table in (workspace.pod_table, workspace.overview.pods_table, workspace.wl_overview.pods_table):
        assert table.model.columns[1:3] == [("app", "label:app"), ("team", "label:team")]
    workspace.open_node_pods("node-a")
    node_pods = workspace.views["node-pods:node-a"][0]
    qtbot.waitUntil(lambda: node_pods.model.rowCount() == 1 and not workspace._loading)
    assert node_pods.model.columns[1:3] == [("app", "label:app"), ("team", "label:team")]
    config.set_labels("Pod", ["owner"])
    assert node_pods.model.columns[1] == ("owner", "label:owner")
    config.set_labels("Node", [])
    assert not any(col[1].startswith("label:") for col in workspace.node_table.model.columns)
    # Reconnected and newly opened workspaces reuse the saved settings.
    window.open_cluster("other")
    other = window.workspace("other")
    qtbot.waitUntil(lambda: other._opened_default and not other._loading)
    assert other.pod_table.model.columns[1] == ("owner", "label:owner")
    assert not any(col[1].startswith("label:") for col in other.node_table.model.columns)


def test_short_headings_persist_independently_and_keep_legacy_lists(tmp_path):
    config = preferences(tmp_path)
    config.set_labels("Node", ["karpenter.sh/nodepool"], {"karpenter.sh/nodepool": "Pool"})
    config.set_labels("Pod", ["karpenter.sh/nodepool"], {"karpenter.sh/nodepool": "Pod Pool"})
    loaded = preferences(tmp_path)
    assert loaded.labels("Node") == ["karpenter.sh/nodepool"]
    assert loaded.headers("Node") == {"karpenter.sh/nodepool": "Pool"}
    assert loaded.headers("Pod") == {"karpenter.sh/nodepool": "Pod Pool"}
    loaded.set_labels("Node", ["karpenter.sh/nodepool", "team"])
    assert preferences(tmp_path).headers("Node") == {"karpenter.sh/nodepool": "Pool"}
    loaded.set_labels("Node", ["team"])
    assert preferences(tmp_path).headers("Node") == {}


def test_short_heading_dialog_updates_existing_key_and_saves(qtbot, tmp_path):
    config = preferences(tmp_path)
    dialog = LabelColumnsDialog("Node", config)
    qtbot.addWidget(dialog)
    dialog.available.setCurrentText("karpenter.sh/nodepool")
    dialog.heading.setText("Pool")
    dialog._add()
    assert dialog.editor.toPlainText() == "karpenter.sh/nodepool = Pool"
    dialog._save()
    loaded = LabelColumnsDialog("Node", preferences(tmp_path))
    qtbot.addWidget(loaded)
    assert loaded.editor.toPlainText() == "karpenter.sh/nodepool = Pool"
    loaded.editor.setPlainText("karpenter.sh/nodepool")
    loaded._save()
    assert preferences(tmp_path).headers("Node") == {}


def test_short_heading_preserves_label_lookup_sort_and_tooltip(qtbot):
    table = ResourceTable([("Name", "name"), ("Roles", "roles")], "Node")
    qtbot.addWidget(table)
    row = SimpleNamespace(name="node", roles="worker", raw={"metadata": {
        "labels": {"karpenter.sh/nodepool": "workers"}}})
    table.set_rows([row])
    table.set_label_columns(["karpenter.sh/nodepool"], {"karpenter.sh/nodepool": "Pool"})
    assert table.model.columns[2] == ("Pool", "label:karpenter.sh/nodepool")
    assert table.model.index(0, 2).data() == "workers"
    assert table.model.index(0, 2).data(Qt.EditRole) == "workers"
    assert table.model.headerData(2, Qt.Horizontal, Qt.ToolTipRole) == "karpenter.sh/nodepool"
    assert row.raw["metadata"]["labels"] == {"karpenter.sh/nodepool": "workers"}
