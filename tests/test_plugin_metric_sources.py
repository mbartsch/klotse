"""#432: plugins add metric sources to the Usage history window (plugin API 13). Core extension point only."""
import json
import math
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from klotse.k8s.models import NodeRow, PodRow
from klotse.plugins import API_VERSION, MetricSeries, MetricSource, MetricsRequest, Plugin
from klotse.plugins.host import MAX_METRIC_POINTS, clean_metric_series, read_metrics, resource_info
from klotse.plugins.manager import _as_plugin
from klotse.plugins.manifest import declarations, read_manifest
from klotse.ui.metrics_dialog import MetricsDialog, SAMPLED
from klotse.ui.workspace import ClusterWorkspace
from test_plugins import manager_with

SOURCE = MetricSource("cloud", "Cloud monitoring", kinds=("Node",),
                      series=(MetricSeries("cpu", "Instance CPU", "%"), MetricSeries("net", "Network in", "bytes/s")))


class CloudMetrics(Plugin):
    id = "cloud"
    name = "Cloud"
    requires_api = 13
    metric_sources = (SOURCE,)

    def __init__(self):
        self.requests, self.fail, self.answer = [], None, None

    def metrics(self, request):
        self.requests.append(request)
        if self.fail:
            raise self.fail
        if self.answer is not None:
            return self.answer
        key = request.resources[0].key
        return {key: {"cpu": [(request.end - 60, 10), (request.end, 12.5)], "net": [(request.end, 2048)]}}


def node_row(name="n1"):
    raw = {"metadata": {"name": name, "uid": f"uid-{name}"}}
    return NodeRow(name, "Ready", "worker", "v1", "10.0.0.1", 4.0, 16.0, "1d", raw)


# ---- the API --------------------------------------------------------------------------------------
def test_api_version_and_valid_metric_sources_are_accepted():
    assert API_VERSION >= 13
    plugin = _as_plugin(CloudMetrics)
    assert plugin.metric_sources == (SOURCE,) and Plugin().metric_sources == () and Plugin().metrics(None) == {}


@pytest.mark.parametrize("sources", [
    ("not a source",),
    [SOURCE],  # a list, not a tuple
    (MetricSource("a", "A", series=()),),  # no series
    (MetricSource("a", "A", kinds=(), series=(MetricSeries("x", "X"),)),),
    (MetricSource("a", "A", kinds="Node", series=(MetricSeries("x", "X"),)),),
    (MetricSource("a", "A", series=(MetricSeries("x", "X"), MetricSeries("x", "Y"))),),
    (MetricSource("a", "A", series=(MetricSeries("", "X"),)),),
    (MetricSource("a", "A", series=("x",)),),
    (MetricSource("a", "A", series=tuple(MetricSeries(f"s{i}", "S") for i in range(17))),),
    (SOURCE, SOURCE),  # repeated source key
])
def test_bad_metric_sources_are_refused(sources):
    class Bad(Plugin):
        id = "bad"
        metric_sources = sources

    with pytest.raises((TypeError, ValueError)):
        _as_plugin(Bad)


def test_the_manifest_declares_metric_sources_and_matches_the_plugin(tmp_path):
    path = tmp_path / "cloud.json"
    path.write_text(json.dumps({"manifest_version": 1, "id": "cloud", "name": "Cloud", "requires_api": 13, "metric_sources": [
        {"key": "cloud", "title": "Cloud monitoring", "kinds": ["Node"],
         "series": [{"key": "cpu", "title": "Instance CPU", "unit": "%"}, {"key": "net", "title": "Network in", "unit": "bytes/s"}]}]}))
    manifest = read_manifest(path)
    assert manifest.metric_sources == (SOURCE,)
    assert declarations(manifest) == declarations(CloudMetrics())


@pytest.mark.parametrize("series", ["cpu", [{"key": "cpu"}], [{"key": "cpu", "title": 1}], [{"key": "cpu", "title": "C", "x": "y"}], []])
def test_a_manifest_with_bad_series_is_refused(tmp_path, series):
    path = tmp_path / "cloud.json"
    path.write_text(json.dumps({"manifest_version": 1, "id": "cloud", "requires_api": 13,
                                "metric_sources": [{"key": "cloud", "title": "C", "series": series}]}))
    with pytest.raises(ValueError):
        read_manifest(path)


def test_only_enabled_plugins_offer_their_sources_for_the_declared_kinds(tmp_path):
    manager = manager_with(tmp_path, CloudMetrics())
    assert [(p.id, s.key) for p, s in manager.metric_sources_for("Node")] == [("cloud", "cloud")]
    assert manager.metric_sources_for("Pod") == []
    manager.set_enabled("cloud", False)
    assert manager.metric_sources_for("Node") == []


# ---- the call and the checks on its answer --------------------------------------------------------
def test_read_metrics_builds_the_request_and_returns_the_declared_series():
    plugin = CloudMetrics()
    info = resource_info(node_row())
    series, warnings = read_metrics(plugin, SOURCE, "Node", "ctx", info, {"region": "x"}, 3600, cache_dir="cache",
                                    api_client="client", now=lambda: 10_000.0)
    request = plugin.requests[0]
    assert isinstance(request, MetricsRequest) and request.source == "cloud" and request.kind == "Node" and request.context == "ctx"
    assert (request.start, request.end, request.step) == (6400.0, 10_000.0, 15.0)
    assert request.resources == [info] and request.settings == {"region": "x"} and request.cache_dir == "cache"
    assert request.api_client == "client"
    assert series == {"cpu": [(9940.0, 10.0), (10_000.0, 12.5)], "net": [(10_000.0, 2048.0)]} and warnings == []


def test_plugin_warnings_are_returned():
    plugin = CloudMetrics()

    def metrics(request):
        request.warn("credentials expired")
        return {}
    plugin.metrics = metrics
    assert read_metrics(plugin, SOURCE, "Node", "ctx", resource_info(node_row()), {}, 900) == ({}, ["credentials expired"])


def test_bad_points_are_left_out_and_counted():
    values = {"cpu": [(3, 1), (1, 2), (2, float("nan")), (True, 1), ("4", 1), (5,), None, (6, math.inf), (7, 7.5)],
              "net": "not a list", "unknown": [(1, 1)]}
    series, dropped = clean_metric_series(SOURCE, values)
    assert series == {"cpu": [(1.0, 2.0), (3.0, 1.0), (7.0, 7.5)]} and dropped == 7


def test_points_past_the_limit_keep_the_latest():
    values = {"cpu": [(t, 1) for t in range(MAX_METRIC_POINTS + 10)]}
    series, dropped = clean_metric_series(SOURCE, values)
    assert len(series["cpu"]) == MAX_METRIC_POINTS and series["cpu"][-1][0] == MAX_METRIC_POINTS + 9 and dropped == 10


@pytest.mark.parametrize("answer", [None, [], "x", {"n1": [1, 2]}])
def test_a_wrong_shape_raises_instead_of_reaching_the_chart(answer):
    plugin = CloudMetrics()
    plugin.answer = answer if answer is not None else 0
    with pytest.raises(ValueError):
        read_metrics(plugin, SOURCE, "Node", "ctx", resource_info(node_row()), {}, 900)


def test_a_missing_resource_is_an_empty_answer():
    plugin = CloudMetrics()
    plugin.answer = {"other": {"cpu": [(1, 1)]}}
    assert read_metrics(plugin, SOURCE, "Node", "ctx", resource_info(node_row()), {}, 900) == ({}, [])


# ---- the Usage history window ---------------------------------------------------------------------
def workspace(qtbot, tmp_path, plugin):
    service = SimpleNamespace(metrics_sample=lambda *a: {}, metrics_available=lambda: True)
    ws = ClusterWorkspace(service, "ctx", plugins=manager_with(tmp_path, plugin))
    qtbot.addWidget(ws)
    return ws


def select(dialog, data):
    dialog.source_box.setCurrentIndex(dialog.source_box.findData(data))


def test_usage_history_shows_a_plugin_series_for_a_node(qtbot, tmp_path):
    plugin = CloudMetrics()
    ws = workspace(qtbot, tmp_path, plugin)
    dialog = ws.show_metrics("Node", node_row())
    data = "plugin:cloud:cloud"
    assert dialog.source_box.findData(data) > 0 and dialog.mode() == SAMPLED  # offered, not switched to
    select(dialog, data)
    qtbot.waitUntil(lambda: bool(dialog.cpu_chart.series), timeout=5000)
    assert threading.current_thread() is threading.main_thread()
    assert dialog.cpu_chart.series == {"n1": [(plugin.requests[0].end - 60, 10.0), (plugin.requests[0].end, 12.5)]}
    assert dialog.cpu_chart.title == "Instance CPU" and dialog.cpu_chart.formatter(12.5) == "12.5 %"
    assert dialog.series_box.isVisibleTo(dialog) and not dialog.mem_chart.isVisibleTo(dialog) and dialog.range_box.isEnabled()
    assert "Cloud monitoring" in dialog.status.text() and "Cloud" in dialog.note.text()
    requests = len(plugin.requests)
    dialog.series_box.setCurrentIndex(1)  # another series: drawn from the same answer, no new request
    assert dialog.cpu_chart.title == "Network in" and dialog.cpu_chart.series == {"n1": [(plugin.requests[0].end, 2048.0)]}
    assert len(plugin.requests) == requests
    select(dialog, SAMPLED)  # back to metrics-server: both charts as before
    assert dialog.cpu_chart.title == "CPU" and dialog.mem_chart.isVisibleTo(dialog) and not dialog.series_box.isVisibleTo(dialog)
    dialog.close()
    ws.shutdown()


def test_a_pod_window_has_no_node_source_and_no_plugins_means_no_extra_source(qtbot, tmp_path):
    ws = workspace(qtbot, tmp_path, CloudMetrics())
    pod = PodRow("web", "shop", "1/1", "Running", 0, "n1", "1d", {"metadata": {"name": "web"}, "spec": {}})
    dialog = ws.show_metrics("Pod", pod)
    assert dialog.source_box.count() == 1
    dialog.close()
    plain = ClusterWorkspace(SimpleNamespace(metrics_sample=lambda *a: {}, metrics_available=lambda: True), "ctx")
    qtbot.addWidget(plain)
    other = plain.show_metrics("Node", node_row())
    assert other.source_box.count() == 1
    other.close()
    ws.shutdown()
    plain.shutdown()


def test_a_failing_or_misshapen_plugin_shows_a_status_not_a_crash(qtbot, tmp_path):
    plugin = CloudMetrics()
    plugin.fail = RuntimeError("throttled")
    ws = workspace(qtbot, tmp_path, plugin)
    dialog = ws.show_metrics("Node", node_row())
    select(dialog, "plugin:cloud:cloud")
    qtbot.waitUntil(lambda: "Could not read" in dialog.status.text(), timeout=5000)
    assert "throttled" in dialog.status.text() and dialog.cpu_chart.series == {}
    plugin.fail, plugin.answer = None, {"n1": {"cpu": [(1, "x"), (2, 3)]}}
    dialog.refresh()
    qtbot.waitUntil(lambda: bool(dialog.cpu_chart.series), timeout=5000)
    assert dialog.cpu_chart.series == {"n1": [(2.0, 3.0)]} and "1 point left out" in dialog.status.text()
    dialog.close()
    ws.shutdown()


def test_a_stale_plugin_reply_is_discarded(qtbot):
    release, seen = threading.Event(), []

    def fetch(seconds):
        seen.append(seconds)
        if len(seen) == 1:
            release.wait(5)
        return {"cpu": {"n1": [(1.0, float(seconds))]}}, []

    dialog = MetricsDialog("Usage history", lambda: {}, available=False)
    qtbot.addWidget(dialog)
    dialog.add_plugin_source("plugin:cloud:cloud", "Cloud monitoring · Cloud", "Cloud", [("cpu", "CPU", "%")], fetch)
    select(dialog, "plugin:cloud:cloud")
    qtbot.waitUntil(lambda: len(seen) == 1, timeout=5000)
    dialog.range_box.setCurrentIndex(2)  # another range while the first read is running
    release.set()
    qtbot.waitUntil(lambda: len(seen) == 2 and bool(dialog.cpu_chart.series), timeout=5000)
    QApplication.processEvents()
    assert seen == [3600, 21600] and dialog.cpu_chart.series == {"n1": [(1.0, 21600.0)]}
    dialog.close()
