import time
from types import SimpleNamespace

from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from klotse.k8s import metrics_history as mh
from klotse.k8s.client import KubeService
from klotse.k8s.models import PodRow
from klotse.ui.line_chart import LineChart
from klotse.ui.metrics_dialog import MetricsDialog
from klotse.ui.workspace import ClusterWorkspace

STAMP = "2026-11-08T10:00:15Z"


def pod_metrics(name="web", containers=(("app", "100m", "64Mi"),), stamp=STAMP):
    return {"metadata": {"name": name}, "timestamp": stamp, "containers": [{"name": c, "usage": {"cpu": cpu, "memory": mem}} for c, cpu, mem in containers]}


def test_metrics_server_objects_become_cores_and_mib():
    stamp, containers = mh.parse_pod(pod_metrics(containers=(("a", "250m", "128Mi"), ("b", "1500000n", "1Gi"))))
    assert stamp == mh.parse_time(STAMP) and containers == {"a": (0.25, 128.0), "b": (0.0015, 1024.0)}
    assert mh.parse_time("garbage") is None and mh.parse_time("") is None
    node = mh.node_sample({"timestamp": STAMP, "usage": {"cpu": "2", "memory": "4Gi"}})
    assert node == {"node": (mh.parse_time(STAMP), 2.0, 4096.0)}


def test_a_pod_with_several_containers_gets_a_total_and_a_workload_a_line_per_few_pods():
    lines = mh.pod_sample(pod_metrics(containers=(("a", "100m", "10Mi"), ("b", "200m", "20Mi"))))
    assert set(lines) == {"a", "b", "total"} and round(lines["total"][1], 3) == 0.3 and lines["total"][2] == 30.0
    assert set(mh.pod_sample(pod_metrics())) == {"app"}  # one container: no separate total
    few = mh.workload_sample([pod_metrics("p1"), pod_metrics("p2")])
    assert set(few) == {"p1", "p2", "total"} and round(few["total"][1], 3) == 0.2
    many = mh.workload_sample([pod_metrics(f"p{i}") for i in range(20)])
    assert set(many) == {"total"}  # too many lines for a chart: the total only


def test_history_keeps_a_sample_once_and_in_time_order():
    history = mh.History(max_points=3)
    sample = {"total": (100.0, 0.1, 10.0)}
    assert history.add(sample, 0) == 1 and history.add(sample, 0) == 0  # the same timestamp again: the same sample
    assert history.add({"total": (90.0, 0.2, 20.0)}, 0) == 0  # older than the last: ignored
    history.add({"total": (130.0, 0.3, 30.0), "x": (None, 1.0, 1.0)}, fallback_time=140.0)  # no timestamp: the fallback stands in
    assert history.series("cpu")["total"] == [(100.0, 0.1), (130.0, 0.3)] and history.series("mem")["x"] == [(140.0, 1.0)]
    for t in (150.0, 160.0, 170.0):
        history.add({"total": (t, 0.0, 0.0)}, 0)
    assert len(history.lines["total"]) == 3 and history.count() == 3 and history.first_time() == 140.0  # a ring: the oldest points go
    assert list(history.series("cpu")) == ["total", "x"]  # totals first
    history.clear()
    assert history.count() == 0 and history.first_time() is None


def row_pod(**kw):
    return PodRow("web", "shop", "1/1", "Running", 0, "n1", "5m", {"spec": {"containers": [
        {"resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "128Mi"}}}]}}, **kw)


def test_reference_lines_come_from_the_pods_resources_and_the_nodes_allocatable():
    assert mh.reference_lines("Pod", row_pod()) == {"cpu": {"request": 0.1, "limit": 0.5}, "mem": {"request": 64.0, "limit": 128.0}}
    node = SimpleNamespace(cpu=4.0, memory=16.0)
    assert mh.reference_lines("Node", node) == {"cpu": {"allocatable": 4.0}, "mem": {"allocatable": 16384.0}}
    bare = PodRow("x", "a", "", "", 0, "", "", {})
    assert mh.reference_lines("Pod", bare) == {"cpu": {}, "mem": {}} and mh.reference_lines("Deployment", node) == {"cpu": {}, "mem": {}}
    assert mh.format_cores(0.25) == "250m" and mh.format_cores(2) == "2.00" and mh.format_mib(512) == "512Mi" and mh.format_mib(2048) == "2.00Gi"


# ---- the service -------------------------------------------------------------------------------------------------
class FakeMetricsApi:
    def __init__(self):
        self.calls = []

    def get_cluster_custom_object(self, group, version, plural, name, **kw):
        self.calls.append(("node", plural, name))
        return {"timestamp": STAMP, "usage": {"cpu": "1", "memory": "1Gi"}}

    def get_namespaced_custom_object(self, group, version, namespace, plural, name, **kw):
        self.calls.append(("pod", namespace, name))
        if name == "gone":
            raise ApiException(status=404, reason="Not Found")
        if name == "denied":
            raise ApiException(status=403, reason="Forbidden")
        return pod_metrics(name)

    def list_namespaced_custom_object(self, group, version, namespace, plural, label_selector="", **kw):
        self.calls.append(("selector", namespace, label_selector))
        return {"items": [pod_metrics("p1"), pod_metrics("p2")]}


def test_the_service_makes_one_small_request_per_sample():
    service = KubeService()
    service._custom = api = FakeMetricsApi()
    assert set(service.metrics_sample("Pod", "shop", "web")) == {"app"}
    assert set(service.metrics_sample("Node", name="n1")) == {"node"}
    assert set(service.metrics_sample("Deployment", "shop", "web", "app=web")) == {"p1", "p2", "total"}
    assert api.calls == [("pod", "shop", "web"), ("node", "nodes", "n1"), ("selector", "shop", "app=web")]
    assert service.metrics_sample("Pod", "shop", "gone") == {}  # no reading yet: not an error
    try:
        service.metrics_sample("Pod", "shop", "denied")
        raise AssertionError("a refusal should be raised")
    except ApiException as error:
        assert error.status == 403


# ---- the chart and the window ------------------------------------------------------------------------------------
def test_the_chart_scales_labels_and_names_the_nearest_values(qtbot):
    chart = LineChart("CPU", mh.format_cores)
    qtbot.addWidget(chart)
    chart.resize(600, 220)
    t = time.time()
    chart.set_data({"total": [(t, 0.1), (t + 30, 0.3)], "app": [(t, 0.05), (t + 30, 0.2)]}, {"limit": 0.5})
    t0, t1, top = chart.ranges()
    assert (t0, t1) == (t, t + 30) and top > 0.5  # the reference line is inside the scale
    text = chart.tooltip_at(chart.plot_rect().right() - 1)
    assert "total: 300m" in text and "app: 200m" in text
    assert chart.tooltip_at(1) == ""  # left of the plot
    pixmap = QPixmap(chart.size())
    chart.render(pixmap)  # painting must not fail
    assert not pixmap.isNull()
    chart.set_data({})
    assert chart.tooltip_at(100) == ""
    chart.render(QPixmap(chart.size()))
    one = LineChart("Memory", mh.format_mib)
    qtbot.addWidget(one)
    one.set_data({"total": [(t, 64.0)]})
    one.render(QPixmap(one.size()))  # a single point is drawn as a dot


def wait_for(qtbot, dialog, count):
    qtbot.waitUntil(lambda: dialog.history.count() >= count and not dialog._busy, timeout=8000)


def test_the_window_samples_on_a_timer_and_charts_the_history(qtbot):
    stamps = iter(range(1000, 1100))
    dialog = MetricsDialog("Usage history: web", lambda: {"total": (float(next(stamps)), 0.1, 64.0)}, {"cpu": {"limit": 0.5}, "mem": {}}, True, interval_s=0.05)
    qtbot.addWidget(dialog)
    wait_for(qtbot, dialog, 3)
    assert "samples since" in dialog.status.text() and dialog.cpu_chart.refs == {"limit": 0.5}
    assert len(dialog.cpu_chart.series["total"]) >= 3 and dialog.mem_chart.series["total"][-1][1] == 64.0
    dialog.pause_button.setChecked(True)
    qtbot.wait(150)
    frozen = dialog.history.count()
    qtbot.wait(300)
    assert dialog.history.count() == frozen and "paused" in dialog.status.text()
    dialog.pause_button.setChecked(False)
    wait_for(qtbot, dialog, frozen + 1)
    dialog.clear()
    assert dialog.history.count() == 0 and dialog.cpu_chart.series == {}
    dialog.accept()
    count = dialog.history.count()
    qtbot.wait(300)
    assert dialog.history.count() == count  # closing stops the sampling


def test_a_sample_that_fails_is_shown_and_the_window_keeps_going(qtbot):
    calls = {"n": 0}

    def sample():
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("forbidden")
        return {"total": (float(2000 + calls["n"]), 0.1, 1.0)}

    dialog = MetricsDialog("x", sample, None, True, interval_s=0.05)
    qtbot.addWidget(dialog)
    wait_for(qtbot, dialog, 1)
    assert "samples since" in dialog.status.text() or "sample since" in dialog.status.text()  # recovered after the first failure
    dialog.accept()


def test_without_the_metrics_api_nothing_is_sampled_and_the_window_says_why(qtbot):
    asked = []
    dialog = MetricsDialog("x", lambda: asked.append(1) or {}, None, False)
    qtbot.addWidget(dialog)
    qtbot.wait(100)
    assert asked == [] and "does not serve the metrics API" in dialog.status.text() and not dialog.timer.isActive()


# ---- the menu and the workspace ------------------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def menu_texts(table, row_index=0):
    texts = []

    def grab():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            texts.extend(a.text() for a in popup.actions() if not a.isSeparator())
            popup.close()
    QTimer.singleShot(100, grab)
    table._context_menu(table.view.visualRect(table.proxy.index(row_index, 0)).center())
    return texts


def test_the_right_click_menu_offers_usage_history_for_pods_nodes_and_workloads_only(qtbot):
    ws = make_ws(qtbot)
    ws.show()
    ws.pod_table.set_rows([row_pod()])
    assert "Usage history…" in menu_texts(ws.pod_table)
    from klotse.ui.resource_table import METRIC_KINDS
    assert METRIC_KINDS == {"Pod", "Node", "Deployment", "StatefulSet", "DaemonSet"}
    assert ws.views["configmaps"][0].kind not in METRIC_KINDS and ws.views["services"][0].kind not in METRIC_KINDS
    assert {ws.views[k][0].kind for k in ("nodes", "deployments", "statefulsets", "daemonsets")} <= METRIC_KINDS


def test_show_metrics_opens_one_window_per_object_and_closes_them_with_the_cluster(qtbot):
    seen = []
    ws = make_ws(qtbot, metrics_sample=lambda *a: seen.append(a) or {"app": (1.0, 0.1, 10.0)}, metrics_available=lambda: True)
    first = ws.show_metrics("Pod", row_pod())
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0] == ("Pod", "shop", "web", "") and first.cpu_chart.refs == {"request": 0.1, "limit": 0.5}
    assert ws.show_metrics("Pod", row_pod()) is first  # asking again brings the same window forward
    node = SimpleNamespace(name="n1", namespace="", cpu=4.0, memory=16.0, raw={})
    second = ws.show_metrics("Node", node)
    assert second is not first and len(ws._metrics_dialogs) == 2
    ws.shutdown()
    assert not first.isVisible() and not second.isVisible()


def test_a_workload_is_sampled_by_its_label_selector_and_refused_without_one(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = []
    ws = make_ws(qtbot, metrics_sample=lambda *a: seen.append(a) or {}, metrics_available=lambda: True)
    deployment = SimpleNamespace(name="web", namespace="shop", raw={"spec": {"selector": {"matchLabels": {"app": "web", "tier": "fe"}}}})
    dialog = ws.show_metrics("Deployment", deployment)
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0][0] == "Deployment" and seen[0][3] in ("app=web,tier=fe", "tier=fe,app=web")
    dialog.close()
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    assert ws.show_metrics("Deployment", SimpleNamespace(name="x", namespace="a", raw={"spec": {}})) is None and "no label selector" in told[0]
    ws.shutdown()


def test_a_service_that_cannot_read_metrics_says_so(qtbot):
    ws = make_ws(qtbot)
    said = []
    ws.message.connect(said.append)
    assert ws.show_metrics("Pod", row_pod()) is None and "cannot read metrics" in said[0]


def test_a_real_node_row_opens_the_window_it_has_no_namespace(qtbot):
    """Regression: NodeRow has no `namespace`; the Nodes list's Usage history did nothing."""
    from klotse.k8s.models import NodeRow
    import dataclasses
    fields = {f.name: ("" if f.type in (str, "str") else 0) for f in dataclasses.fields(NodeRow) if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
    fields.update(name="n1", cpu=4.0, memory=16.0, raw={})
    node = NodeRow(**fields)
    assert not hasattr(node, "namespace")
    seen = []
    ws = make_ws(qtbot, metrics_sample=lambda *a: seen.append(a) or {"node": (1.0, 0.5, 100.0)}, metrics_available=lambda: True,
                 find_prometheus=lambda: [], prometheus_check=lambda ref: True)
    dialog = ws.show_metrics("Node", node)
    assert dialog is not None and dialog.windowTitle() == "Usage history: Node n1 (ctx)"
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0] == ("Node", "", "n1", "") and dialog.cpu_chart.refs == {"allocatable": 4.0}
    assert ws.show_metrics("Node", node) is dialog
    ws.shutdown()
