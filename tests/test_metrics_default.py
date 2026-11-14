"""Metrics source defaults: precedence (own entry, then the default for all clusters, then automatic), stale entries, and the
Preferences > Metrics panel saving its changes. Uses a QSettings file in tmp_path; never the user's settings."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.ui import metrics_preferences
from klotse.ui.metrics_preferences import MetricsPanel
from klotse.ui.metrics_sources import MetricsSources


def sources(tmp_path):
    return MetricsSources(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat))


def test_with_nothing_saved_a_cluster_looks_for_one_automatically(tmp_path):
    s = sources(tmp_path)
    assert s.get("prod") == "" and s.get_via("prod") == "proxy"


def test_the_default_applies_to_a_cluster_without_its_own_entry(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428")
    assert s.get("prod") == "monitoring/vmsingle:8428"
    assert s.own_ref("prod") == ""


def test_the_cluster_own_entry_wins_over_the_default(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428")
    s.set("prod", "obs/prometheus:80")
    assert s.get("prod") == "obs/prometheus:80"
    assert s.get("staging") == "monitoring/vmsingle:8428"


def test_clearing_the_cluster_entry_falls_back_to_the_default(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428")
    s.set("prod", "obs/prometheus:80")
    s.set("prod", "")
    assert s.get("prod") == "monitoring/vmsingle:8428"


def test_clearing_the_default_means_look_for_one_automatically(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428")
    s.set_default("")
    assert s.get("prod") == "" and s.get_default() == ("", "proxy", 0)


def test_a_forward_default_keeps_its_port(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428", via="forward", local=9090)
    assert s.get_default() == ("monitoring/vmsingle:8428", "forward", 9090)
    assert s.get_via("prod") == "forward" and s.get_local("prod") == 9090


def test_a_cluster_entry_with_its_own_proxy_route_ignores_the_default_route(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428", via="forward", local=9090)
    s.set("prod", "obs/prometheus:80")
    assert s.get_via("prod") == "proxy"


def test_stale_entries_are_the_ones_not_in_the_kubeconfig(tmp_path):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    s.set("old-lab", "obs/prometheus:80")
    assert s.stale(["prod", "staging"]) == ["old-lab"]


def test_the_default_is_not_an_entry_so_it_is_never_stale(tmp_path):
    s = sources(tmp_path)
    s.set_default("monitoring/vmsingle:8428")
    assert s.stale(["prod"]) == [] and s.entries() == {}


def test_panel_saves_default_and_edits_and_removes_cluster_entries(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    s.set("old-lab", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod"])
    assert panel.stale_names() == ["old-lab"]
    panel.mode.setCurrentIndex(1)
    panel.default_edit.setText("monitoring/vmsingle:8428")
    panel.changes["prod"] = {**panel.saved["prod"], "ref": "obs/other:9090"}
    panel.changes["old-lab"] = None
    panel.save()
    assert s.get_default()[0] == "monitoring/vmsingle:8428"
    assert s.get("prod") == "obs/other:9090"
    assert s.entries().keys() == {"prod"}


def test_panel_refuses_a_bad_default_and_saves_nothing(qapp, tmp_path):
    s = sources(tmp_path)
    panel = MetricsPanel(s, ["prod"])
    panel.mode.setCurrentIndex(1)
    panel.default_edit.setText("not an address")
    try:
        panel.save()
    except ValueError:
        pass
    else:
        raise AssertionError("a bad address must be refused")
    assert s.get_default()[0] == ""


def test_an_unreadable_kubeconfig_marks_nothing_as_stale(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("old-lab", "obs/prometheus:80")
    panel = MetricsPanel(s, None)
    assert panel.stale_names() == []


def test_remove_unused_asks_first_and_defaults_to_no(qapp, tmp_path, monkeypatch):
    s = sources(tmp_path)
    s.set("old-lab", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod"])
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args) or QMessageBox.No)
    panel.remove_unused()
    assert asked and panel.changes == {}  # the answer was No: nothing staged


def test_kubeconfig_contexts_returns_none_when_the_file_is_missing(tmp_path):
    assert metrics_preferences.kubeconfig_contexts(str(tmp_path / "missing")) is None
