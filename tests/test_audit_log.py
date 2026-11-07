import os
import json
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.audit import describe, target_of
from klotse.k8s.client import KubeService, ReadOnlyError
from klotse.ui.audit_dialog import AuditDialog
from klotse.ui.audit_log import AuditLog, MAX_BYTES
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService


def test_describe_names_objects_and_never_their_contents():
    body = {"kind": "Secret", "metadata": {"name": "db", "namespace": "shop"}, "data": {"password": "c2VjcmV0"}}
    assert describe(body) == "Secret shop/db"
    assert describe({"data": {"password": "x"}}) == "" and describe({"spec": {"suspend": True}}) == ""
    assert target_of(["Deployment", "api", "shop", 3, {"spec": {"x": 1}}, body]) == "Deployment api shop 3 Secret shop/db"
    assert "c2VjcmV0" not in target_of([body])


def test_records_round_trip_newest_first_and_skip_damaged_lines(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    log.record("prod", "scale", "Deployment api shop 3")
    log.record("prod", "delete_pod", "web-1 shop", "failed", "ApiException: (403)\nsecond line")
    with open(log.path, "a", encoding="utf-8") as f:
        f.write("not json\n")
    entries = log.entries()
    assert [e["action"] for e in entries] == ["delete_pod", "scale"]
    assert entries[0]["result"] == "failed" and entries[0]["detail"] == "ApiException: (403)"
    assert entries[1]["cluster"] == "prod" and len(entries[0]["time"]) == 19


def test_the_log_rotates_and_never_raises(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    with open(log.path, "w", encoding="utf-8") as f:
        f.write("x" * (MAX_BYTES + 10))
    log.record("c", "scale", "t")
    assert (tmp_path / "audit.log.1").exists() and len(log.entries()) == 1
    bad = AuditLog(tmp_path / "audit.log" / "nested" / "file")  # a path that cannot be written
    assert bad.record("c", "a") is None
    log.clear()
    assert log.entries() == [] and not (tmp_path / "audit.log.1").exists()


class Service(KubeService):
    def __init__(self):
        super().__init__()
        self.calls = []

    def delete_pod(self, name, namespace, force=False):
        return None
    delete_pod = KubeService.delete_pod

    def cordon_node(self, name, unschedulable):
        raise RuntimeError("boom\nmore")
    cordon_node = KubeService.cordon_node


def test_mutating_calls_report_success_and_failure_but_refusals_are_not_changes():
    seen = []
    service = KubeService.__new__(KubeService)
    service.read_only = False
    service.audit = lambda *a: seen.append(a)
    service._core = SimpleNamespace(delete_namespaced_pod=lambda *a, **k: None,
                                    patch_node=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    service.delete_pod("web-1", "shop")
    try:
        service.cordon_node("n1", True)
    except RuntimeError:
        pass
    assert seen[0] == ("delete_pod", "web-1 shop", "ok", "") and seen[1][0] == "cordon_node" and seen[1][2] == "failed"
    service.read_only = True
    try:
        service.delete_pod("web-1", "shop")
    except ReadOnlyError:
        pass
    assert len(seen) == 2  # nothing was changed, nothing is logged
    service.audit = lambda *a: 1 / 0  # a broken log must not break the change
    service.read_only = False
    service.delete_pod("web-1", "shop")


def test_workspace_writes_cluster_and_action(qtbot, tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    service = KubeService.__new__(KubeService)
    service.read_only = False
    service._core = SimpleNamespace(delete_namespaced_pod=lambda *a, **k: None)
    ws = ClusterWorkspace(service, "prod-eu", audit_log=log)
    qtbot.addWidget(ws)
    service.delete_pod("web-1", "shop")
    ws.record_change("drain", "node-a")
    assert [(e["cluster"], e["action"], e["result"]) for e in log.entries()] == [("prod-eu", "drain", "started"),
                                                                                  ("prod-eu", "delete_pod", "ok")]


def test_dialog_lists_filters_and_clears(qtbot, tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit.log")
    log.record("a", "scale", "Deployment api shop 3")
    log.record("b", "delete_pod", "web-1 shop", "failed", "no")
    dialog = AuditDialog(log)
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 2 and dialog.table.item(0, 4).text() == "✖ failed"
    dialog.filter.setText("scale")
    assert [dialog.table.isRowHidden(r) for r in range(2)] == [True, False]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    dialog._clear()
    assert dialog.table.rowCount() == 0 and log.entries() == []


def test_main_window_keeps_the_log_beside_the_settings_file(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    assert os.path.normpath(win.audit_log.path) == os.path.normpath(str(tmp_path / "audit.log"))
