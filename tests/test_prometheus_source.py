import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s import prometheus as pm
from klotse.k8s.client import KubeService
from klotse.ui.metrics_dialog import REMOTE, SAMPLED, MetricsDialog
from klotse.ui.metrics_sources import MetricsSources
from klotse.ui.workspace import ClusterWorkspace


def service(name, ns="monitoring", ports=((9090, "http"),), labels=None):
    return {"metadata": {"name": name, "namespace": ns, "labels": labels or {}}, "spec": {"ports": [{"port": p, "name": n, "protocol": "TCP"} for p, n in ports]}}


def test_services_are_recognised_by_name_and_labels_and_ranked():
    found = pm.candidates([
        service("prometheus-alertmanager"), service("prometheus-node-exporter", ports=((9100, "metrics"),)), service("grafana", ports=((80, "http"),)),
        service("kube-state-metrics"), service("prometheus-server", ports=((80, "http"),)), service("vmsingle-main", ns="vm", ports=((8428, "http"),)),
        service("vmselect-main", ns="vm", ports=((8481, "http"),)), service("vminsert-main", ns="vm", ports=((8480, "http"),)),
        service("my-prom", labels={"app.kubernetes.io/name": "prometheus"}, ns="apps", ports=((9090, "web"), (9093, "alert"))),
        service("web", ports=((80, "http"),))])
    names = [(r["namespace"], r["name"], r["port"], r["kind"], r["prefix"]) for r in found]
    assert names[0][:2] == ("monitoring", "prometheus-server") and names[0][2:] == (80, "prometheus", "")
    assert ("vm", "vmselect-main", 8481, "vmselect", "/select/0/prometheus") in names and ("vm", "vmsingle-main", 8428, "victoria", "") in names
    assert ("apps", "my-prom", 9090, "prometheus", "") in names
    assert not any(n[1] in ("prometheus-alertmanager", "prometheus-node-exporter", "grafana", "kube-state-metrics", "vminsert-main", "web") for n in names)
    assert pm.candidates([service("prometheus-server", ports=())]) == []


def test_a_typed_reference_is_validated():
    assert pm.parse_ref("monitoring/prometheus-server:80") == {"namespace": "monitoring", "name": "prometheus-server", "port": 80, "scheme": "", "prefix": ""}
    assert pm.parse_ref(" https:monitoring/prom:9091/ ")["scheme"] == "https"
    assert pm.parse_ref("vm/vmselect:8481/select/0/prometheus/")["prefix"] == "/select/0/prometheus"
    assert pm.ref_text(pm.parse_ref("vm/vmselect:8481/select/0/prometheus")) == "vm/vmselect:8481/select/0/prometheus"
    for bad in ("prometheus", "monitoring/prom", "monitoring/prom:abc", "monitoring/prom:70000", "a b/c:1", ""):
        with pytest.raises(ValueError):
            pm.parse_ref(bad)


def test_the_service_proxy_path_and_query_string():
    ref = {"namespace": "monitoring", "name": "prometheus-server", "port": 80, "scheme": "", "prefix": ""}
    path = pm.range_path(ref, 'up{job="a b"}', 100.4, 200.6, 15)
    parsed = urlparse(path)
    assert parsed.path == "/api/v1/namespaces/monitoring/services/prometheus-server:80/proxy/api/v1/query_range"
    assert parse_qs(parsed.query) == {"query": ['up{job="a b"}'], "start": ["100"], "end": ["201"], "step": ["15"]}
    vm = {"namespace": "vm", "name": "vmselect", "port": 8481, "scheme": "https", "prefix": "/select/0/prometheus"}
    assert urlparse(pm.instant_path(vm, "up")).path == "/api/v1/namespaces/vm/services/https:vmselect:8481/proxy/select/0/prometheus/api/v1/query"
    assert pm.step_for(900) == 15 and pm.step_for(604800) == 2520


def test_queries_are_per_container_per_pod_or_for_the_node_and_escape_names():
    pod = pm.queries("Pod", "shop", ["web-1"])
    assert pod["cpu"] == 'sum by (container) (rate(container_cpu_usage_seconds_total{namespace="shop",pod="web-1",container!="",container!="POD"}[5m]))'
    assert pod["mem"].startswith("sum by (container) (container_memory_working_set_bytes{")
    many = pm.queries("Deployment", "shop", ["web-a", "web.b"], window="2m")
    assert 'pod=~"web-a|web\\\\.b"' in many["cpu"] and "sum by (pod)" in many["cpu"] and "[2m]" in many["cpu"]
    node = pm.queries("Node", "", (), ["n1", "10.0.0.5"])
    assert 'instance=~"n1(:.*)?|10\\\\.0\\\\.0\\\\.5(:.*)?"' in node["cpu"] and 'id="/"' in node["mem"] and 'node=~"n1|10\\\\.0\\\\.0\\\\.5"' in node["cpu_alt"]
    assert pm.quote_label('a"b\\c') == 'a\\"b\\\\c'


def matrix(*series):
    return json.dumps({"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": metric, "values": [[t, v] for t, v in values]} for metric, values in series]}})


def test_answers_are_read_and_totals_are_added():
    text = matrix(({"container": "app"}, [(100, "0.1"), (115, "0.2"), (130, "NaN")]), ({"container": "side"}, [(100, "0.05"), (115, "0.05")]))
    lines = pm.parse_matrix(text, "container")
    assert lines == {"app": [(100.0, 0.1), (115.0, 0.2)], "side": [(100.0, 0.05), (115.0, 0.05)]}  # NaN skipped
    total = pm.with_total(lines)
    assert list(total) == ["total", "app", "side"] and total["total"] == [(100.0, 0.15000000000000002), (115.0, 0.25)]
    assert pm.with_total({"only": [(1.0, 1.0)]}) == {"only": [(1.0, 1.0)]}
    crowd = {f"p{i}": [(1.0, 1.0)] for i in range(12)}
    assert list(pm.with_total(crowd)) == ["total"]  # too many lines to chart: the total only
    assert pm.parse_matrix(matrix(({}, [(1, "2")]))) == {"total": [(1.0, 2.0)]}
    assert pm.parse_matrix(matrix()) == {}
    cpu, mem = pm.to_chart({"total": [(1.0, 0.5)]}, {"total": [(1.0, 2 * 2**20)]})
    assert cpu == {"total": [(1.0, 0.5)]} and mem == {"total": [(1.0, 2.0)]}


def test_error_answers_and_non_prometheus_answers_are_explained():
    with pytest.raises(pm.PrometheusError, match="bad_data|parse error"):
        pm.parse_matrix(json.dumps({"status": "error", "errorType": "bad_data", "error": "parse error: bad_data"}))
    with pytest.raises(pm.PrometheusError, match="not JSON"):
        pm.parse_matrix("<html>nginx</html>")


# ---- the service, through a fake API server proxy ------------------------------------------------------------------------
class Proxy:
    """api.rest_client.request answering like Prometheus behind the API server proxy."""

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def request(self, method, url, headers=None, _request_timeout=None):
        self.urls.append(url)
        query = parse_qs(urlparse(url).query).get("query", [""])[0]
        for needle, body in self.answers.items():
            if needle in query:
                return SimpleNamespace(data=body.encode() if isinstance(body, str) else body, status=200)
        return SimpleNamespace(data=matrix().encode(), status=200)


def prom_service(answers, nodes=None):
    svc = KubeService()
    cfg = SimpleNamespace(host="https://api.example", auth_settings=lambda: {})
    svc._api_client = SimpleNamespace(configuration=cfg, default_headers={}, rest_client=Proxy(answers))
    svc._core = SimpleNamespace(read_node=lambda name, **k: SimpleNamespace(status=SimpleNamespace(addresses=[SimpleNamespace(type="InternalIP", address="10.0.0.5")])))
    svc.pods_by_selector = lambda ns, sel: [("web-a", ["c"], "Running"), ("web-b", ["c"], "Running")]
    return svc


REF = {"namespace": "monitoring", "name": "prometheus-server", "port": 80, "scheme": "", "prefix": ""}


def test_a_pods_history_is_read_per_container_with_a_total():
    t = time.time()
    svc = prom_service({"rate(": matrix(({"container": "a"}, [(t, "0.1")]), ({"container": "b"}, [(t, "0.2")])),
                        "working_set": matrix(({"container": "a"}, [(t, str(2**20 * 64))]), ({"container": "b"}, [(t, str(2**20 * 32))]))})
    cpu, mem = svc.prometheus_history(REF, "Pod", "shop", "web-1", seconds=3600)
    assert set(cpu) == {"total", "a", "b"} and round(cpu["total"][0][1], 3) == 0.3 and mem["total"][0][1] == 96.0
    assert len(svc._api_client.rest_client.urls) == 2  # one query for CPU, one for memory


def test_the_history_url_goes_through_the_api_server_proxy_of_the_service():
    svc = prom_service({})
    svc.prometheus_history(REF, "Pod", "shop", "web-1", seconds=3600)
    urls = svc._api_client.rest_client.urls
    assert len(urls) == 2 and all(u.startswith("https://api.example/api/v1/namespaces/monitoring/services/prometheus-server:80/proxy/api/v1/query_range?") for u in urls)
    assert "step=15" in urls[0]


def test_a_workload_uses_its_current_pods_and_a_node_its_name_and_address_then_the_node_label():
    svc = prom_service({})
    svc.prometheus_history(REF, "Deployment", "shop", "web", "app=web", 3600)
    query = parse_qs(urlparse(svc._api_client.rest_client.urls[0]).query)["query"][0]
    assert 'pod=~"web-a|web-b"' in query
    svc.pods_by_selector = lambda ns, sel: []
    assert svc.prometheus_history(REF, "Deployment", "shop", "web", "app=web", 3600) == ({}, {})  # no pods: nothing to ask
    node = prom_service({})
    node.prometheus_history(REF, "Node", name="n1", seconds=3600)
    urls = [parse_qs(urlparse(u).query)["query"][0] for u in node._api_client.rest_client.urls]
    assert len(urls) == 4 and "instance=~" in urls[0] and "instance=~" in urls[1] and "node=~" in urls[2] and "node=~" in urls[3]  # nothing came back: both forms tried
    answered = prom_service({"instance=~": matrix(({}, [(time.time(), "1")]))})
    answered.prometheus_history(REF, "Node", name="n1", seconds=3600)
    assert len(answered._api_client.rest_client.urls) == 2  # the first form worked: no second try


def test_check_and_find():
    ok = prom_service({"up": json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}})})
    assert ok.prometheus_check(REF) is True
    with pytest.raises(pm.PrometheusError):
        prom_service({"up": json.dumps({"status": "error", "error": "no"})}).prometheus_check(REF)
    with pytest.raises(ValueError):
        prom_service({"up": "<html>"}).prometheus_check(REF)
    svc = KubeService()
    svc.list_resource = lambda key: [SimpleNamespace(raw=service("prometheus-server"))]
    assert svc.find_prometheus()[0]["name"] == "prometheus-server"
    svc.list_resource = lambda key: (_ for _ in ()).throw(PermissionError("no"))
    assert svc.find_prometheus() == []


# ---- the window ------------------------------------------------------------------------------------------------------
def remote(calls, delay=0.0):
    def fetch(seconds):
        calls.append(seconds)
        t = time.time()
        return {"total": [(t - 60, 0.1), (t, 0.2)]}, {"total": [(t - 60, 64.0), (t, 70.0)]}
    return fetch


def test_the_window_switches_to_prometheus_with_a_range_and_back_to_sampling(qtbot):
    dialog = MetricsDialog("x", lambda: {"total": (time.time(), 0.1, 1.0)}, None, True, interval_s=3600)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.history.count() == 1 and not dialog._busy, timeout=5000)
    assert dialog.mode() == SAMPLED and not dialog.range_box.isEnabled()
    calls = []
    dialog.set_prometheus("monitoring/prometheus-server:80", remote(calls))
    assert dialog.mode() == REMOTE and dialog.range_box.isEnabled() and "History from the Prometheus" in dialog.note.text()
    qtbot.waitUntil(lambda: bool(calls) and not dialog._busy, timeout=5000)
    assert calls == [3600] and len(dialog.cpu_chart.series["total"]) == 2 and "1 hour" in dialog.status.text() and "monitoring/prometheus-server:80" in dialog.status.text()
    dialog.range_box.setCurrentIndex(4)  # 7 days
    qtbot.waitUntil(lambda: calls == [3600, 604800] and not dialog._busy, timeout=5000)
    dialog.source_box.setCurrentIndex(0)
    assert dialog.mode() == SAMPLED and not dialog.range_box.isEnabled() and "starts when the window opens" in dialog.note.text()
    dialog.clear_prometheus()
    assert dialog.source_box.count() == 1 and dialog.fetch_remote is None
    dialog.accept()


def test_prometheus_errors_and_empty_answers_are_explained(qtbot):
    dialog = MetricsDialog("x", lambda: {}, None, False, interval_s=3600)
    qtbot.addWidget(dialog)
    assert "does not serve the metrics API" in dialog.status.text() and "Source…" in dialog.status.text()
    dialog.set_prometheus("p", lambda seconds: (_ for _ in ()).throw(PermissionError("forbidden")))
    qtbot.waitUntil(lambda: "Could not read p" in dialog.status.text(), timeout=5000)
    assert "forbidden" in dialog.status.text()
    dialog.set_prometheus("p", lambda seconds: ({}, {}))
    qtbot.waitUntil(lambda: "0 points" in dialog.status.text(), timeout=5000)
    assert "no data" in dialog.cpu_chart.placeholder
    dialog.accept()


# ---- the workspace and the saved setting -------------------------------------------------------------------------------
class Fake:
    def __init__(self, found=(), ok=True):
        self.found, self.ok, self.checked, self.finds, self.history = list(found), ok, [], 0, []

    def metrics_available(self):
        return True

    def metrics_sample(self, *a):
        return {}

    def find_prometheus(self):
        self.finds += 1
        return list(self.found)

    def prometheus_check(self, ref):
        self.checked.append(ref["name"])
        if self.ok is not True and ref["name"] != self.ok:
            raise ConnectionError("503 no endpoints")
        return True

    def prometheus_history(self, ref, kind, ns, name, selector, seconds):
        self.history.append((ref["name"], kind, ns, name, seconds))
        return {"total": [(time.time(), 0.1)]}, {"total": [(time.time(), 5.0)]}


def pod_row():
    from klotse.k8s.models import PodRow
    return PodRow("web", "shop", "1/1", "Running", 0, "n1", "5m", {"spec": {"containers": []}})


def ws_with(qtbot, fake, tmp_path):
    from klotse.ui.customizations import LabelSettings
    ws = ClusterWorkspace(fake, "ctx", label_settings=LabelSettings(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)))
    qtbot.addWidget(ws)
    return ws


CANDIDATES = [{"namespace": "monitoring", "name": "prom-a", "port": 80, "scheme": "", "prefix": "", "kind": "prometheus"},
              {"namespace": "monitoring", "name": "prom-b", "port": 9090, "scheme": "", "prefix": "", "kind": "prometheus"}]


def test_a_found_service_is_checked_added_and_remembered_for_the_next_window(qtbot, tmp_path):
    fake = Fake(CANDIDATES, ok="prom-b")  # the first candidate does not answer, the second does
    ws = ws_with(qtbot, fake, tmp_path)
    dialog = ws.show_metrics("Pod", pod_row())
    qtbot.waitUntil(lambda: dialog.mode() == REMOTE, timeout=5000)
    assert fake.checked == ["prom-a", "prom-b"] and "monitoring/prom-b:9090" in dialog.source_box.currentText()
    qtbot.waitUntil(lambda: bool(fake.history) and not dialog._busy, timeout=5000)
    assert fake.history[0] == ("prom-b", "Pod", "shop", "web", 3600)
    node = SimpleNamespace(name="n1", namespace="", cpu=4.0, memory=16.0, raw={})
    second = ws.show_metrics("Node", node)
    assert second.mode() == REMOTE and fake.finds == 1  # remembered: the cluster is not searched again
    ws.shutdown()


def test_no_service_means_metrics_server_only_and_the_search_is_not_repeated(qtbot, tmp_path):
    fake = Fake([])
    ws = ws_with(qtbot, fake, tmp_path)
    dialog = ws.show_metrics("Pod", pod_row())
    qtbot.waitUntil(lambda: ws._prometheus is False, timeout=5000)
    assert dialog.mode() == SAMPLED and dialog.source_box.count() == 1
    other = SimpleNamespace(name="n1", namespace="", cpu=4.0, memory=16.0, raw={})
    ws.show_metrics("Node", other)
    qtbot.wait(200)
    assert fake.finds == 1
    ws.shutdown()


def test_source_dialog_saves_a_typed_service_checks_it_and_an_empty_answer_goes_back_to_searching(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    fake = Fake([])
    ws = ws_with(qtbot, fake, tmp_path)
    dialog = ws.show_metrics("Pod", pod_row())
    qtbot.waitUntil(lambda: ws._prometheus is False, timeout=5000)
    answers = iter([("monitoring/my-prom:9090", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: dialog.mode() == REMOTE, timeout=5000)
    assert fake.checked[-1] == "my-prom" and ws.metrics_sources.get("ctx") == "monitoring/my-prom:9090" and fake.finds == 1  # typed: nothing was searched for
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("not a service", True))
    dialog.source_button.click()
    assert warned and "namespace/service:port" in warned[0] and ws.metrics_sources.get("ctx") == "monitoring/my-prom:9090"  # not saved
    fake.found = CANDIDATES[:1]
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", True))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: fake.finds == 2 and bool(ws._prometheus) and dialog.mode() == REMOTE, timeout=5000)
    assert ws.metrics_sources.get("ctx") == "" and fake.checked[-1] == "prom-a"
    ws.shutdown()


def test_a_typed_service_that_does_not_answer_is_reported_in_the_window(qtbot, tmp_path):
    fake = Fake([], ok="other")
    ws = ws_with(qtbot, fake, tmp_path)
    ws.metrics_sources.set("ctx", "monitoring/broken:9090")
    dialog = ws.show_metrics("Pod", pod_row())
    qtbot.waitUntil(lambda: "was not used" in dialog.status.text(), timeout=5000)
    assert "monitoring/broken:9090" in dialog.status.text() and "503" in dialog.status.text() and dialog.mode() == SAMPLED
    ws.shutdown()


def test_the_setting_is_kept_per_cluster_and_cleared_with_empty_text(tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    sources = MetricsSources(store)
    assert sources.get("a") == ""
    sources.set("a", "monitoring/prom:80")
    sources.set("b", " vm/vmselect:8481/select/0/prometheus ")
    assert MetricsSources(store).get("a") == "monitoring/prom:80" and MetricsSources(store).get("b") == "vm/vmselect:8481/select/0/prometheus"
    sources.set("a", "")
    assert MetricsSources(store).get("a") == "" and MetricsSources(store).get("b") != ""
