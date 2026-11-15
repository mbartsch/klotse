"""Preferences > Metrics: metrics-server only as a source, and the Test buttons (default and per row). Test is asked of a fake tester here;
the real check runs on a connected cluster. Uses a QSettings file in tmp_path; never the user's settings."""
from PySide6.QtCore import QSettings

from klotse.ui.metrics_preferences import MetricsPanel, valid_source
from klotse.ui.metrics_sources import METRICS_SERVER, MetricsSources


def sources(tmp_path):
    return MetricsSources(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat))


def test_metrics_server_is_a_valid_source_in_any_case():
    assert valid_source(" metrics-server ") == METRICS_SERVER
    assert valid_source("METRICS-SERVER") == METRICS_SERVER


def test_a_bad_prometheus_address_is_still_refused():
    try:
        valid_source("not an address")
    except ValueError:
        return
    raise AssertionError("a bad address must be refused")


def test_the_default_can_be_metrics_server_only(qapp, tmp_path):
    s = sources(tmp_path)
    panel = MetricsPanel(s, ["prod"])
    panel.mode.setCurrentIndex(2)
    panel.save()
    assert s.get_default()[0] == METRICS_SERVER
    assert s.get("prod") == METRICS_SERVER
    assert MetricsPanel(s, ["prod"]).mode.currentIndex() == 2  # reopening shows the same choice


def test_a_cluster_can_be_set_to_metrics_server_only_and_saved(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod"])
    panel.changes["prod"] = {**panel.saved["prod"], "ref": METRICS_SERVER}
    panel.save()
    assert s.get("prod") == METRICS_SERVER


def test_test_on_a_row_asks_the_tester_for_that_cluster_and_shows_the_answer(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    asked = []

    def tester(context, text, done, auth):
        asked.append((context, text))
        done("obs/prometheus:80 answered the Prometheus API.")

    panel = MetricsPanel(s, ["prod"], tester=tester)
    panel._test_row("prod")
    assert asked == [("prod", "obs/prometheus:80")]
    assert panel.result.text() == "obs/prometheus:80 answered the Prometheus API."
    assert s.get("prod") == "obs/prometheus:80"  # testing saves nothing


def test_default_test_uses_the_chosen_default_text_without_saving(qapp, tmp_path):
    s = sources(tmp_path)
    asked = []
    panel = MetricsPanel(s, [], tester=lambda context, text, done, auth: asked.append((context, text)))
    panel.mode.setCurrentIndex(1)
    panel.default_edit.setText("monitoring/vmsingle:8428")
    panel._test_default()
    assert asked == [(None, "monitoring/vmsingle:8428")]
    assert s.get_default()[0] == ""  # not saved by testing


def test_default_test_with_metrics_server_asks_for_metrics_server(qapp, tmp_path):
    asked = []
    panel = MetricsPanel(sources(tmp_path), [], tester=lambda context, text, done, auth: asked.append(text))
    panel.mode.setCurrentIndex(2)
    panel._test_default()
    assert asked == [METRICS_SERVER]


def test_test_without_a_tester_says_so(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("prod", "obs/prometheus:80")
    panel = MetricsPanel(s, ["prod"])
    panel._test_row("prod")
    assert panel.result.text() == "Testing is not available here."


def test_test_of_automatic_default_says_there_is_nothing_to_test(qapp, tmp_path):
    panel = MetricsPanel(sources(tmp_path), [], tester=lambda *args: None)
    panel.mode.setCurrentIndex(0)
    panel._test_default()
    assert "Nothing to test" in panel.result.text()
