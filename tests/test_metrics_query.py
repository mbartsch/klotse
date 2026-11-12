"""Workload > Metrics query…: quick PromQL on the Prometheus / VictoriaMetrics source. Pure parsing and presets first, then the dialog and the
workspace path with a fake service (no cluster, no network)."""
import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s import prometheus as pm
from klotse.ui.customizations import LabelSettings
from klotse.ui.metrics_query_dialog import MetricsQueryDialog
from klotse.ui.workspace import ClusterWorkspace


def vector(*series):
    return json.dumps({"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": metric, "value": [1700000000, value]} for metric, value in series]}})


def test_a_vector_answer_gives_labels_and_latest_numbers():
    found = pm.parse_vector(vector(({"pod": "web-1", "container": "app"}, "0.25"), ({"pod": "web-2"}, "NaN"), ({"pod": "web-3"}, "3")))
    assert [(labels, value) for labels, value, _ in found] == [({"pod": "web-1", "container": "app"}, 0.25), ({"pod": "web-3"}, 3.0)]
    assert found[0][2] == 1700000000.0  # the time of the sample is kept, for the table
    assert pm.series_text({"__name__": "x", "pod": "web-1", "container": "app"}) == '__name__="x", pod="web-1", container="app"'
    assert pm.series_text({}) == "(no labels)"


def test_an_error_answer_or_a_non_json_answer_is_explained():
    with pytest.raises(pm.PrometheusError, match="parse error"):
        pm.parse_vector(json.dumps({"status": "error", "error": "parse error at char 3"}))
    with pytest.raises(pm.PrometheusError, match="not JSON"):
        pm.parse_vector("<html>503</html>")


@pytest.mark.parametrize("kind, pod_pattern, replicas", [
    ("Deployment", r'pod=~"web-[a-z0-9]+-[a-z0-9]+"', "kube_deployment_status_replicas_available"),
    ("StatefulSet", r'pod=~"web-[0-9]+"', "kube_statefulset_status_replicas_ready"),
    ("DaemonSet", r'pod=~"web-[a-z0-9]+"', "kube_daemonset_status_number_ready"),
])
def test_workload_presets_follow_the_controllers_pod_names(kind, pod_pattern, replicas):
    presets = dict(pm.workload_queries(kind, "shop", "web"))
    assert any(replicas in query for query in presets.values())
    assert all(pod_pattern in query for query in presets.values() if replicas not in query)
    assert all('namespace="shop"' in query for query in presets.values())


def test_names_are_escaped_inside_the_quoted_query():
    queries = pm.workload_queries("Deployment", 'sh"op', "web.v1")
    text = " ".join(query for _, query in queries)
    assert 'namespace="sh\\"op"' in text  # the quote in the namespace cannot end the label value
    assert r"web\\.v1-" in text  # the dot is escaped for the regular expression and again for the PromQL string


class Fake:
    def __init__(self, series):
        self.series, self.queries = series, []

    def metrics_available(self):
        return True

    def metrics_sample(self, *a):
        return {}

    def find_prometheus(self):
        return [{"namespace": "monitoring", "name": "prom", "port": 9090, "scheme": "", "prefix": "", "kind": "prometheus"}]

    def prometheus_check(self, ref):
        return True

    def prometheus_query(self, ref, promql):
        self.queries.append(promql)
        return self.series


def workspace(qtbot, tmp_path, fake):
    ws = ClusterWorkspace(fake, "ctx", label_settings=LabelSettings(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)))
    qtbot.addWidget(ws)
    return ws


def deployment_row():
    return SimpleNamespace(name="web", namespace="shop", raw={"spec": {}})


def test_the_dialog_shows_the_latest_value_of_each_series(qtbot):
    answers = []

    def run(promql, ok, bad):
        answers.append(promql)
        ok([({"pod": "web-1", "container": "app"}, 0.5, 1700000000.0), ({"pod": "web-2"}, 12.0, 1700000030.0)])

    dialog = MetricsQueryDialog("Metrics", "monitoring/prom:9090", [("CPU, cores", "sum(up)")], run)
    qtbot.addWidget(dialog)
    dialog.run_query()
    assert answers == ["sum(up)"] and dialog.table.rowCount() == 2
    assert dialog.table.item(0, 0).text() == 'pod="web-1", container="app"' and dialog.table.item(0, 1).text() == "0.5"
    assert dialog.table.item(0, 2).text() != "" and dialog.table.horizontalHeaderItem(2).text() == "At"
    assert dialog.status.text() == "2 series." and dialog.run_button.isEnabled()


def test_a_failed_or_empty_query_says_so_and_nothing_is_run_without_text(qtbot):
    dialog = MetricsQueryDialog("Metrics", "src", [], lambda promql, ok, bad: bad("parse error at char 3"))
    qtbot.addWidget(dialog)
    dialog.run_query()
    assert dialog.status.text() == "Write a query first."
    dialog.query.setText("sum(")
    dialog.run_query()
    assert dialog.status.text() == "The query failed: parse error at char 3" and dialog.table.rowCount() == 0


def test_workload_menu_query_runs_the_preset_against_the_found_source(qtbot, tmp_path):
    fake = Fake([({"pod": "web-1"}, 0.2, 1700000000.0)])
    ws = workspace(qtbot, tmp_path, fake)
    ws.show_metrics_query("Deployment", deployment_row())
    qtbot.waitUntil(lambda: any(getattr(d, "key", None) for d in ws._metrics_dialogs), timeout=10000)
    dialog = next(d for d in ws._metrics_dialogs if getattr(d, "key", None))
    qtbot.waitUntil(lambda: "monitoring/prom:9090" in dialog.source.text(), timeout=10000)
    dialog.run_query()
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1, timeout=10000)
    assert "kube_deployment_status_replicas_available" in fake.queries[-1]  # the first preset for a Deployment is its replica count
    ws.show_metrics_query("Deployment", deployment_row())  # asking again brings the same window forward, it does not open another
    assert len([d for d in ws._metrics_dialogs if getattr(d, "key", None)]) == 1
    ws.shutdown()


def test_the_right_click_menu_offers_metrics_query_for_workloads_only(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from klotse.k8s.models import DeploymentRow
    ws = workspace(qtbot, tmp_path, Fake([]))
    ws.show()
    table = ws.views["deployments"][0]
    table.set_rows([DeploymentRow("web", "shop", "1/1", 1, 1, "1d", {"spec": {"replicas": 1}})])
    texts = []

    def grab():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            texts.extend(a.text() for a in popup.actions() if not a.isSeparator())
            popup.close()
    QTimer.singleShot(100, grab)
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert "Metrics query…" in texts
    ws.shutdown()


def test_pod_and_node_presets_name_the_object_exactly():
    pod = dict(pm.workload_queries("Pod", "shop", "web-1"))
    assert 'pod="web-1"' in pod["Restarts in the last hour (kube-state-metrics)"] or "web-1" in " ".join(pod.values())
    assert 'pod="web-1"' in pod["CPU, cores"] and "pod=~" not in pod["CPU, cores"]
    node = pm.workload_queries("Node", "", "worker-1")
    assert [title for title, _ in node] == ["CPU, cores", "Memory, MiB"]
    assert "worker-1" in node[0][1] and node[1][1].endswith("/1048576")


def test_a_window_opens_without_a_source_and_says_why(qtbot, tmp_path):
    class NoSource(Fake):
        def find_prometheus(self):
            return []

    fake = NoSource([])
    ws = workspace(qtbot, tmp_path, fake)
    ws.show_metrics_query("Deployment", deployment_row())
    qtbot.waitUntil(lambda: any(getattr(d, "key", None) for d in ws._metrics_dialogs), timeout=10000)
    dialog = next(d for d in ws._metrics_dialogs if getattr(d, "key", None))
    qtbot.waitUntil(lambda: "No source" in dialog.status.text(), timeout=10000)
    assert dialog.source_button.isEnabled() and fake.queries == []
    dialog.query.setText("up")
    dialog.run_query()
    qtbot.waitUntil(lambda: "No Prometheus or VictoriaMetrics source is set" in dialog.status.text(), timeout=10000)
    ws.shutdown()


def test_a_range_answer_becomes_one_line_per_series():
    text = json.dumps({"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"pod": "web-1"}, "values": [[1700000000, "0.5"], [1700000060, "NaN"], [1700000120, "0.7"]]},
        {"metric": {"pod": "web-2"}, "values": [[1700000000, "2"]]}]}})
    lines = pm.parse_matrix_series(text)
    assert lines == {'pod="web-1"': [(1700000000.0, 0.5), (1700000120.0, 0.7)], 'pod="web-2"': [(1700000000.0, 2.0)]}


def test_the_graph_button_draws_each_series(qtbot):
    drawn = []

    def run_range(promql, seconds, ok, bad):
        assert seconds == 3600
        ok({'pod="web-1"': [(1.0, 0.5), (2.0, 0.7)], 'pod="web-2"': [(1.0, 2.0)]})

    dialog = MetricsQueryDialog("Metrics", "src", [], lambda promql, ok, bad: ok([]), None, run_range=run_range)
    qtbot.addWidget(dialog)
    dialog.graph_range.setCurrentIndex(1)  # 1 hour
    dialog.query.setText("sum(up)")
    dialog.graph_query()
    assert set(dialog.chart.series) == {'pod="web-1"', 'pod="web-2"'}
    assert dialog.status.text().startswith("2 lines") and dialog.graph_button.isEnabled()
