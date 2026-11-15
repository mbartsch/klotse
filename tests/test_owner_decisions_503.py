"""#503, the owner's decisions: a wrong filter shows no rows, every listed kind can be deleted (with a question), and
several selected rows get no single-object actions in Details."""
from types import SimpleNamespace as NS

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.k8s import client as client_module
from klotse.k8s.resources import DELETABLE_KINDS, SPECS
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.overview import POD_COLUMNS
from klotse.ui.resource_table import ResourceTable
from test_filter_expr_table import ROWS


@pytest.fixture
def table(qtbot):
    t = ResourceTable(POD_COLUMNS, "Pod", customizable=True)
    qtbot.addWidget(t)
    t.set_rows(ROWS)
    return t


def visible(table):
    table._apply_filter()  # the text box applies after a 200 ms debounce; the test does not wait for it
    return table.proxy.rowCount()


def test_a_wrong_filter_expression_shows_no_rows_until_it_is_right(table):
    assert visible(table) == len(ROWS)
    table.filter.setText("restarts >")  # unfinished expression
    assert visible(table) == 0 and not table.filter_note.isHidden()
    table.filter.setText("restarts > 1")
    assert visible(table) == 2 and table.filter_note.isHidden()
    table.filter.setText("web")  # plain text
    assert visible(table) == 2


def test_every_listed_kind_and_the_workload_kinds_can_be_deleted():
    assert {s.kind for s in SPECS} <= DELETABLE_KINDS
    assert {"Deployment", "StatefulSet", "ReplicaSet", "ConfigMap", "Secret", "Service", "Ingress", "PersistentVolumeClaim",
            "DaemonSet", "Job", "CronJob"} <= DELETABLE_KINDS
    assert "Pod" not in DELETABLE_KINDS and "Node" not in DELETABLE_KINDS  # they keep their own actions


class FakeApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
        return call


def test_delete_goes_to_the_right_api_for_a_workload(monkeypatch):
    service = client_module.KubeService.__new__(client_module.KubeService)
    service.read_only = False
    apps, core = FakeApi(), FakeApi()
    service._apis = {"apps": apps, "core": core}
    service.delete_resource("Deployment", "web", "shop", "uid-1")
    assert apps.calls and apps.calls[0][0] == "delete_namespaced_deployment" and apps.calls[0][1] == ("web", "shop")
    with pytest.raises(ValueError):
        service.delete_resource("Pod", "web", "shop", "uid-1")


def test_several_selected_rows_show_a_count_and_no_single_row_actions(qtbot, table):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    table.selection_count_changed.connect(lambda n: panel.show_many(n) if n > 1 else None)
    table.view.selectAll()
    assert panel.identity_title.text() == f"{len(ROWS)} selected"
    assert sum(1 for i in range(panel.action_layout.count()) if panel.action_layout.itemAt(i).widget()) == 0
    assert not panel.identity_uid.isVisible()
