from PySide6.QtCore import Qt

from klotse.k8s.models import NodeRow
from klotse.ui.resource_table import ResourceTable


def test_node_filter_searches_columns_labels_annotations_and_refresh(qtbot):
    table = ResourceTable([("Name", "name"), ("Status", "status")], "Node")
    qtbot.addWidget(table)
    def node(name, metadata):
        return NodeRow(name, "Ready", "worker", "v1", "", 4, 8, "1d", {"metadata": metadata})
    table.set_rows([
        node("alpha", {"labels": {"team": "Payments", "pool": "Spot"},
                       "annotations": {"example.com/owner": "Alice"}}),
        node("beta", {"labels": None, "annotations": None}),
    ])
    for query in ("alpha", "PAYMENTS", "team=Payments", "pool", "example.com/owner", "ALICE"):
        table.filter.setText(query)
        qtbot.waitUntil(lambda: table.proxy.rowCount() == 1)
        assert table.proxy.index(0, 0).data() == "alpha"
    assert table.model.index(0, 0).data(Qt.DisplayRole) == "alpha"
    table.filter.setText("Ready")
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 2)
    table.filter.setText("missing")
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 0)
    table.set_rows([node("gamma", {"annotations": {"owner": "missing"}})])
    assert table.proxy.rowCount() == 1
    assert table.proxy.index(0, 0).data() == "gamma"
