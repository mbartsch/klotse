"""Preferences > Metrics: Add… for a cluster with no source. A connected cluster offers the services found on it; a disconnected one takes a
typed address. Discovery is a fake here; the real one lists Services on a connected cluster. Uses a QSettings file in tmp_path."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui import metrics_preferences
from klotse.ui.metrics_preferences import AddSourceDialog, MetricsPanel
from klotse.ui.metrics_sources import MetricsSources


def sources(tmp_path):
    return MetricsSources(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat))


def test_a_connected_cluster_lists_the_services_found_on_it(qapp):
    asked = []

    def discover(name, done):
        asked.append(name)
        done(["monitoring/vmsingle:8428", "obs/prometheus:80"])

    dialog = AddSourceDialog(["prod"], discover)
    assert asked == ["prod"]
    items = [dialog.service.itemText(i) for i in range(dialog.service.count())]
    assert items == ["monitoring/vmsingle:8428", "obs/prometheus:80"]
    assert "Found 2" in dialog.status.text()


def test_a_disconnected_cluster_takes_a_typed_address(qapp):
    dialog = AddSourceDialog(["prod"], lambda name, done: done(None))
    assert dialog.service.count() == 0
    assert "not connected" in dialog.status.text()
    dialog.service.setEditText("monitoring/prometheus-server:80")
    assert dialog.chosen() == ("prod", "monitoring/prometheus-server:80")


def test_a_connected_cluster_with_nothing_found_says_so(qapp):
    dialog = AddSourceDialog(["prod"], lambda name, done: done([]))
    assert "No Prometheus or VictoriaMetrics service found" in dialog.status.text()


def test_without_discovery_the_dialog_is_manual(qapp):
    dialog = AddSourceDialog([], None)
    dialog.cluster.setEditText("lab-kind")
    assert "not connected" in dialog.status.text()
    assert dialog.chosen() == ("lab-kind", "")


def test_add_saves_the_chosen_source_for_a_cluster_without_one(qapp, tmp_path, monkeypatch):
    s = sources(tmp_path)
    s.set("staging", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod", "staging"], discoverer=lambda name, done: done(["monitoring/vmsingle:8428"]))
    # Only the cluster without a source is offered in the dialog.
    offered = []

    class FakeDialog:
        def __init__(self, clusters, discover, parent=None):
            offered.append(clusters)
            self.discover = discover

        def exec(self):
            return QDialog.Accepted

        def chosen(self):
            return "prod", "monitoring/vmsingle:8428"

        def deleteLater(self):
            pass

    monkeypatch.setattr(metrics_preferences, "AddSourceDialog", FakeDialog)
    panel.add_source()
    assert offered == [["prod"]]
    panel.save()
    assert s.get("prod") == "monitoring/vmsingle:8428"
    assert s.get("staging") == "obs/prometheus:80"


def test_add_refuses_a_cluster_that_already_has_a_source(qapp, tmp_path, monkeypatch):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod"])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def chosen(self):
            return "prod", "monitoring/vmsingle:8428"

        def deleteLater(self):
            pass

    monkeypatch.setattr(metrics_preferences, "AddSourceDialog", FakeDialog)
    panel.add_source()
    assert warned and "already has a source" in warned[0]
    assert panel.changes == {}


def test_add_refuses_a_bad_address_and_stages_nothing(qapp, tmp_path, monkeypatch):
    panel = MetricsPanel(sources(tmp_path), ["prod"])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def chosen(self):
            return "prod", "not an address"

        def deleteLater(self):
            pass

    monkeypatch.setattr(metrics_preferences, "AddSourceDialog", FakeDialog)
    panel.add_source()
    assert warned and panel.changes == {}
