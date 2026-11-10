from types import SimpleNamespace

from PySide6.QtGui import QGuiApplication

from klotse.ui.detail_panel import DetailPanel


def test_workload_identity_header_is_consistent_and_copies_qualified_name_and_uid(qtbot):
    row = SimpleNamespace(
        table_kind="Deployment",
        name="api",
        namespace="shop",
        secret_data={},
        raw={"metadata": {"name": "api", "namespace": "shop", "uid": "12345678-very-long-uid"},
             "spec": {"replicas": 3}, "status": {"readyReplicas": 2}},
    )
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(row)

    assert panel.identity_title.text() == "Deployment · api"
    assert panel.identity_scope.text() == "Namespace: shop"
    assert panel.identity_status.text() == "2 of 3 ready"
    assert panel.identity_uid.text() == "UID: 12345678-very-long-uid"
    panel.copy_identity_button.click()
    assert QGuiApplication.clipboard().text() == "shop/api"
    panel.copy_uid_button.click()
    assert QGuiApplication.clipboard().text() == "12345678-very-long-uid"
    assert "Deployment · api" not in panel.formatted.toPlainText()


def test_node_identity_header_copies_provider_id_and_handles_missing_identity(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    node = SimpleNamespace(
        table_kind="Node", name="worker-1", namespace="", secret_data={},
        raw={"kind": "Node", "metadata": {"name": "worker-1", "uid": "node-uid"},
             "spec": {"providerID": "cloud://region/very-long-instance-id"},
             "status": {"conditions": [{"type": "Ready", "status": "False"}]}},
    )
    panel.show_row(node)
    assert panel.identity_title.text() == "Node · worker-1"
    assert panel.identity_scope.text() == "Cluster-scoped"
    assert panel.identity_status.text() == "Not Ready"
    assert panel.identity_provider.text() == "Provider ID: cloud://region/very-long-instance-id"
    panel.copy_provider_button.click()
    assert QGuiApplication.clipboard().text() == "cloud://region/very-long-instance-id"

    missing = SimpleNamespace(raw={}, secret_data={})
    panel.show_row(missing)
    assert panel.identity_title.text() == "Resource · (unnamed)"
    assert panel.identity_scope.text() == "Cluster-scoped"
    assert panel.identity_status.text() == "Unknown"
    assert not panel.copy_uid_button.isVisible()
    assert not panel.copy_provider_button.isVisible()
