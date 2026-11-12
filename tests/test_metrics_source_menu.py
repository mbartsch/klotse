"""Cluster list > Metrics source…: the usage-history source chosen from the cluster menu is saved for that cluster, like Usage history > Source….
The dialog itself is covered in test_prometheus_source.py; here the menu's path into the workspace and what gets saved."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.ui.customizations import LabelSettings
from klotse.ui.metrics_sources import MetricsSources
from klotse.ui.workspace import ClusterWorkspace


class Stub:
    def metrics_available(self):
        return True

    def metrics_sample(self, *a):
        return {}


def workspace(qtbot, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    ws = ClusterWorkspace(Stub(), "prod-eu", label_settings=LabelSettings(store))
    qtbot.addWidget(ws)
    return ws, store


def test_the_chosen_source_is_saved_for_this_cluster_only(qtbot, tmp_path, monkeypatch):
    ws, store = workspace(qtbot, tmp_path)
    monkeypatch.setattr(ws, "_ask_prometheus_source", lambda: ("monitoring/vmselect:8481/select/0/prometheus", "proxy", 0, "", ""))
    ws.configure_metrics_source()
    saved = MetricsSources(store)
    assert saved.get("prod-eu") == "monitoring/vmselect:8481/select/0/prometheus" and saved.get_via("prod-eu") == "proxy"
    assert saved.get("other") == ""
    ws.shutdown()


def test_a_cancelled_dialog_changes_nothing(qtbot, tmp_path, monkeypatch):
    ws, store = workspace(qtbot, tmp_path)
    MetricsSources(store).set("prod-eu", "monitoring/prom:9090")
    monkeypatch.setattr(ws, "_ask_prometheus_source", lambda: None)
    ws.configure_metrics_source()
    assert MetricsSources(store).get("prod-eu") == "monitoring/prom:9090"
    ws.shutdown()


def test_an_invalid_service_is_refused_and_not_saved(qtbot, tmp_path, monkeypatch):
    ws, store = workspace(qtbot, tmp_path)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    monkeypatch.setattr(ws, "_ask_prometheus_source", lambda: ("not a service", "proxy", 0, "", ""))
    ws.configure_metrics_source()
    assert warned and "namespace/service:port" in warned[0]
    assert MetricsSources(store).get("prod-eu") == ""
    ws.shutdown()
