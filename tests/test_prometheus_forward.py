"""Usage history read from Prometheus / VictoriaMetrics through a port-forward (#382): the saved choice, the query transport, the Source…
dialog and the forward's life cycle. A small Python script stands in for kubectl and a local HTTP server for the metrics store."""
import http.server
import sys
import threading
import time

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s import prometheus as pm
from klotse.k8s.client import KubeService
from klotse.ui.customizations import LabelSettings
from klotse.ui.metrics_dialog import REMOTE, SAMPLED
from klotse.ui.metrics_sources import MetricsSources
from klotse.ui.port_forward import explain_failure
from klotse.ui.prometheus_source_dialog import AUTO, FORWARD, PROXY, PrometheusSourceDialog, split_ref
from klotse.ui.workspace import ClusterWorkspace

ACTIVE = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 9090', flush=True)\n"
          "for _ in range(600):\n    time.sleep(0.05)\n")
FAILS = "import sys\nprint('error: unable to listen on any of the requested ports: [{9090 9090}]', file=sys.stderr, flush=True)\nsys.exit(1)\n"
REF = {"namespace": "monitoring", "name": "my-prom", "port": 9090, "scheme": "", "prefix": "/select/0/prometheus"}


def fake_kubectl(code):
    return lambda context, kubeconfig: [sys.executable, "-u", "-c", code]


# ---- settings and paths -------------------------------------------------------------------------------------------
def test_the_choice_of_a_forward_is_kept_per_cluster(tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    sources = MetricsSources(store)
    sources.set("a", "monitoring/prom:80")
    sources.set("b", "vm/vmselect:8481/select/0/prometheus", "forward", 50000)
    again = MetricsSources(store)
    assert (again.get_via("a"), again.get_local("a")) == ("proxy", 0)
    assert (again.get("b"), again.get_via("b"), again.get_local("b")) == ("vm/vmselect:8481/select/0/prometheus", "forward", 50000)
    sources.set("b", "vm/vmselect:8481", "proxy")
    assert MetricsSources(store).get_via("b") == "proxy" and MetricsSources(store).get_local("b") == 0
    sources.set("b", "")
    assert MetricsSources(store).get("b") == "" and MetricsSources(store).get("a") == "monitoring/prom:80"


def test_a_ref_with_a_local_port_is_asked_on_the_local_address_without_the_proxy():
    local = {**REF, "local_port": 54321}
    assert pm.instant_path(local, "up").startswith("/select/0/prometheus/api/v1/query?")
    assert pm.range_path(local, "up", 1, 2, 15).startswith("/select/0/prometheus/api/v1/query_range?")
    assert "/proxy/" in pm.instant_path(REF, "up")


# ---- the query transport ------------------------------------------------------------------------------------------
@pytest.fixture
def metrics_store():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            ok = self.path.startswith("/select/0/prometheus/api/v1/query")
            body = b'{"status":"success","data":{"resultType":"vector","result":[]}}' if ok else b"forbidden"
            self.send_response(200 if ok else 403)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], seen
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_the_service_check_goes_to_the_forwarded_port(metrics_store):
    port, seen = metrics_store
    assert KubeService.prometheus_check(object.__new__(KubeService), {**REF, "local_port": port}) is True
    assert seen and seen[0].startswith("/select/0/prometheus/api/v1/query?")


def test_a_refused_query_says_so_and_names_the_port(metrics_store):
    port, _ = metrics_store
    with pytest.raises(pm.PrometheusError, match=rf"127\.0\.0\.1:{port}.*refused \(HTTP 403\).*path prefix"):
        KubeService.prometheus_check(object.__new__(KubeService), {**REF, "prefix": "/wrong", "local_port": port})


def test_a_forward_nobody_listens_on_is_reported():
    with pytest.raises(pm.PrometheusError, match="could not connect to the port-forward"):
        KubeService.prometheus_check(object.__new__(KubeService), {**REF, "local_port": 1})


def test_an_https_forward_to_a_plain_server_is_reported_not_retried_as_plain_text():
    with pytest.raises(pm.PrometheusError, match="could not connect|certificate"):
        KubeService.prometheus_check(object.__new__(KubeService), {**REF, "scheme": "https", "local_port": 1})


def test_kubectls_failures_are_made_plainer():
    assert "not allowed" in explain_failure('Error from server (Forbidden): pods "x" is forbidden')
    assert "already in use" in explain_failure("error: unable to listen on any of the requested ports: [{9090 9090}]")
    assert explain_failure("Cannot start kubectl: is it installed and on the PATH?").startswith("Cannot start kubectl")
    assert explain_failure("") != ""


# ---- the Source… dialog -------------------------------------------------------------------------------------------
def test_the_dialog_splits_and_joins_the_reference(qtbot):
    assert split_ref("vm/vmselect:8481/select/0/prometheus") == ("vm/vmselect:8481", "/select/0/prometheus")
    dialog = PrometheusSourceDialog("vm/vmselect:8481/select/0/prometheus", FORWARD, 50000)
    qtbot.addWidget(dialog)
    assert dialog.mode() == FORWARD and dialog.local.isEnabled() and dialog.values() == ("vm/vmselect:8481/select/0/prometheus", FORWARD, 50000)
    dialog.proxy.setChecked(True)
    assert not dialog.local.isEnabled() and dialog.values() == ("vm/vmselect:8481/select/0/prometheus", PROXY, 0)
    dialog.auto.setChecked(True)
    assert not dialog.service.isEnabled() and dialog.values() == ("", PROXY, 0)
    assert PrometheusSourceDialog("", "proxy", 0).mode() == AUTO


def test_the_dialog_refuses_what_cannot_be_used_and_stays_open(qtbot, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    dialog = PrometheusSourceDialog("", "proxy", 0)
    qtbot.addWidget(dialog)
    dialog.forward.setChecked(True)
    dialog._accept()
    assert "namespace/service:port" in warned[-1]
    dialog.service.setText("monitoring/prom:9090")
    dialog.local.setText("70000")
    dialog._accept()
    assert "not a port number" in warned[-1]
    dialog.local.setText("")
    dialog.service.setText("https:monitoring/prom:9090")  # https is allowed through a forward (SSL option)
    dialog._accept()
    assert len(warned) == 2 and dialog.result() == QDialog.Accepted


def test_choosing_a_candidate_fills_the_fields(qtbot):
    dialog = PrometheusSourceDialog("", "proxy", 0)
    qtbot.addWidget(dialog)
    dialog.set_candidates([{**REF, "kind": "vmselect"}])
    dialog.candidates_menu.actions()[0].trigger()
    assert dialog.service.text() == "monitoring/my-prom:9090" and dialog.prefix.text() == "/select/0/prometheus"


# ---- the workspace: the forward's life cycle ----------------------------------------------------------------------
class Fake:
    def __init__(self):
        self.refs = []

    def metrics_available(self):
        return True

    def metrics_sample(self, *a):
        return {}

    def find_prometheus(self):
        return []

    def prometheus_check(self, ref):
        self.refs.append(dict(ref))
        return True

    def prometheus_history(self, ref, kind, ns, name, selector, seconds):
        return {"total": [(time.time(), 0.1)]}, {"total": [(time.time(), 5.0)]}


def pod_row():
    from klotse.k8s.models import PodRow
    return PodRow("web", "shop", "1/1", "Running", 0, "n1", "5m", {"spec": {"containers": []}})


@pytest.fixture
def workspace(qtbot, tmp_path):
    made = []

    def make(code=ACTIVE):
        fake = Fake()
        ws = ClusterWorkspace(fake, "ctx", label_settings=LabelSettings(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)))
        qtbot.addWidget(ws)
        ws.port_forwards.base = fake_kubectl(code)
        made.append(ws)
        return ws, fake
    yield make
    for ws in made:
        ws.shutdown()
        for fw in ws.port_forwards.items:
            if fw.process is not None:
                fw.process.waitForFinished(3000)


def choose(ws, answer):
    ws._ask_prometheus_source = lambda: answer if len(answer) == 5 else (*answer, "", "")


def test_a_saved_forward_is_not_started_by_opening_a_window(qtbot, workspace):
    ws, fake = workspace()
    ws.metrics_sources.set("ctx", "monitoring/my-prom:9090", "forward")
    dialog = ws.show_metrics("Pod", pod_row())
    qtbot.waitUntil(lambda: "port-forward" in dialog.status.text(), timeout=10000)
    assert "Source…" in dialog.status.text() and ws.port_forwards.items == [] and fake.refs == [] and dialog.mode() == SAMPLED


def test_choosing_the_forward_starts_it_shows_it_and_reads_through_it(qtbot, workspace):
    ws, fake = workspace()
    dialog = ws.show_metrics("Pod", pod_row())
    choose(ws, ("monitoring/my-prom:9090/select/0/prometheus", "forward", 0))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: dialog.mode() == REMOTE, timeout=20000)
    forward = ws.port_forwards.items[0]
    assert (forward.target, forward.namespace, forward.remote, forward.origin) == ("svc/my-prom", "monitoring", 9090, "Usage history")
    assert fake.refs[-1]["local_port"] == 54321 and fake.refs[-1]["prefix"] == "/select/0/prometheus"
    assert (ws.metrics_sources.get_via("ctx"), ws.metrics_sources.get("ctx")) == ("forward", "monitoring/my-prom:9090/select/0/prometheus")
    assert any(getattr(ws.log_tabs.widget(i), "key", "") == "port-forwards" for i in range(ws.log_tabs.count()))


def test_closing_the_window_stops_the_forward_but_another_window_keeps_it(qtbot, workspace):
    ws, _ = workspace()
    first = ws.show_metrics("Pod", pod_row())
    choose(ws, ("monitoring/my-prom:9090", "forward", 0))
    first.source_button.click()
    qtbot.waitUntil(lambda: first.mode() == REMOTE, timeout=20000)
    forward = ws.port_forwards.items[0]
    second = ws.show_metrics("Node", type("N", (), {"name": "n1", "namespace": "", "cpu": 4.0, "memory": 16.0, "raw": {}})())
    qtbot.waitUntil(lambda: second.mode() == REMOTE, timeout=20000)
    assert len(ws.port_forwards.items) == 1  # the running forward is reused, nothing new starts
    first.close()
    qtbot.wait(200)
    assert forward.running
    second.close()
    qtbot.waitUntil(lambda: forward.state == "stopped", timeout=8000)


def test_a_forward_that_fails_is_explained_in_the_window(qtbot, workspace):
    ws, fake = workspace(FAILS)
    dialog = ws.show_metrics("Pod", pod_row())
    choose(ws, ("monitoring/my-prom:9090", "forward", 9090))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: "through a port-forward" in dialog.status.text(), timeout=10000)
    assert "already in use" in dialog.status.text() and dialog.mode() == SAMPLED and fake.refs == []


def test_choosing_the_proxy_again_stops_the_forward(qtbot, workspace):
    ws, _ = workspace()
    dialog = ws.show_metrics("Pod", pod_row())
    choose(ws, ("monitoring/my-prom:9090", "forward", 0))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: dialog.mode() == REMOTE, timeout=20000)
    forward = ws.port_forwards.items[0]
    choose(ws, ("monitoring/my-prom:9090", "proxy", 0))
    dialog.source_button.click()
    qtbot.waitUntil(lambda: forward.state == "stopped", timeout=8000)
    assert ws.metrics_sources.get_via("ctx") == "proxy"


def test_a_chosen_forward_can_be_kept_in_saved_forwards(qtbot, workspace):
    from klotse.ui.saved_forwards import SavedForwards
    ws, _ = workspace()
    ws._remember_source_forward("monitoring/my-prom:9090", 15432)
    assert SavedForwards(ws.label_settings.store).for_context("ctx") == [
        {"ctx": "ctx", "kind": "Service", "ns": "monitoring", "name": "my-prom", "remote": 9090, "local": 15432}]
