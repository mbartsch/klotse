"""Port-forward usage history over SSL (#382 follow-up): "Open in browser" uses https for an SSL forward, the query transport picks https and
certificate checks from the options, the Preferences defaults and per-cluster overrides resolve, and the Test button reports the result.
Nothing here touches a real cluster or network: kubectl is a small script, and the https connection is a recording stub."""
import ssl
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s import prometheus as pm
from klotse.k8s.client import KubeService
from klotse.ui import port_forward as pf
from klotse.ui.customizations import LabelSettings
from klotse.ui.metrics_sources import ForwardDefaults, MetricsSources
from klotse.ui.prometheus_source_dialog import PrometheusSourceDialog
from klotse.ui.workspace import ClusterWorkspace

ACTIVE = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 9090', flush=True)\n"
          "for _ in range(600):\n    time.sleep(0.05)\n")
REF = {"namespace": "monitoring", "name": "my-prom", "port": 9090, "scheme": "https", "prefix": "/select/0/prometheus"}


@pytest.fixture
def manager(qtbot):
    made = pf.PortForwards(base=lambda context, kubeconfig: [sys.executable, "-u", "-c", ACTIVE])
    yield made
    made.stop_all()
    for fw in made.items:
        if fw.process is not None:
            fw.process.waitForFinished(3000)


def test_open_in_browser_uses_https_for_an_ssl_forward_and_http_otherwise(qtbot, manager, monkeypatch):
    opened = []
    monkeypatch.setattr(pf.webbrowser, "open", lambda url: opened.append(url))
    panel = pf.PortForwardPanel(manager, context="ctx")
    qtbot.addWidget(panel)
    panel.show()
    ssl_fw = manager.start("ctx", "monitoring", "Service", "my-prom", 9090, scheme="https")
    qtbot.waitUntil(lambda: ssl_fw.state == "active", timeout=8000)
    panel.table.selectRow(manager.items.index(ssl_fw))
    panel.open_button.click()
    plain_fw = manager.start("ctx", "shop", "Pod", "web", 80, local=0)
    qtbot.waitUntil(lambda: plain_fw.state == "active", timeout=8000)
    panel.table.selectRow(manager.items.index(plain_fw))
    panel.open_button.click()
    assert opened == [f"https://{ssl_fw.address}/", f"http://{plain_fw.address}/"]


class FakeConnection:
    """Records how the https connection was made and answers a success."""
    made = []

    def __init__(self, host, port, timeout=None, context=None):
        FakeConnection.made.append({"host": host, "port": port, "context": context})
        self.status = 200

    def request(self, method, path, headers=None):
        self.path = path

    def getresponse(self):
        return self

    def read(self, _limit=None):
        return b'{"status":"success","data":{"resultType":"vector","result":[]}}'

    def close(self):
        pass


def test_the_https_query_uses_tls_and_checks_certificates_unless_told_otherwise(monkeypatch):
    import http.client
    FakeConnection.made = []
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    service = object.__new__(KubeService)
    assert service.prometheus_check({**REF, "local_port": 54321, "insecure": False}) is True
    checked = FakeConnection.made[-1]
    assert checked["host"] == "127.0.0.1" and checked["port"] == 54321
    assert checked["context"].verify_mode == ssl.CERT_REQUIRED and checked["context"].check_hostname
    service.prometheus_check({**REF, "local_port": 54321, "insecure": True})
    ignored = FakeConnection.made[-1]["context"]
    assert ignored.verify_mode == ssl.CERT_NONE and not ignored.check_hostname


def test_a_plain_forward_does_not_use_tls(monkeypatch):
    import http.client
    seen = []
    monkeypatch.setattr(http.client, "HTTPConnection", lambda host, port, timeout=None: seen.append(port) or FakeConnection(host, port))
    service = object.__new__(KubeService)
    service.prometheus_check({**REF, "scheme": "", "local_port": 54322})
    assert seen == [54322]


def test_preferences_and_the_cluster_override_resolve_into_the_forward_options(qtbot, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    defaults = ForwardDefaults(store)
    assert (defaults.tls, defaults.insecure) == (False, False)
    defaults.set(True, True)
    assert (ForwardDefaults(store).tls, ForwardDefaults(store).insecure) == (True, True)
    sources = MetricsSources(store)
    sources.set("prod", "monitoring/my-prom:9090", "forward", 0, tls="off", insecure="")
    assert (sources.get_tls("prod"), sources.get_insecure("prod"), sources.get_via("prod")) == ("off", "", "forward")
    assert sources.get_tls("other") == ""  # no entry: the Preferences setting applies

    class Stub:
        def metrics_available(self):
            return True

        def metrics_sample(self, *a):
            return {}

    ws = ClusterWorkspace(Stub(), "prod", label_settings=LabelSettings(store))
    qtbot.addWidget(ws)
    assert ws._forward_options() == (False, True)  # SSL overridden off for this cluster, certificates from Preferences
    assert ws._forward_options("other") == (True, True)  # another cluster follows Preferences
    ws.shutdown()


class ProxyFake:
    def metrics_available(self):
        return True

    def metrics_sample(self, *a):
        return {}

    def find_prometheus(self):
        return []

    def prometheus_check(self, ref):
        return True


def test_test_button_reports_a_proxy_service_that_answers(qtbot, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    ws = ClusterWorkspace(ProxyFake(), "ctx", label_settings=LabelSettings(store))
    qtbot.addWidget(ws)
    dialog = PrometheusSourceDialog("monitoring/my-prom:9090", "proxy", 0, "", "", False, False)
    qtbot.addWidget(dialog)
    ws._test_source(dialog)
    qtbot.waitUntil(lambda: "answers" in dialog.test_result.text(), timeout=8000)
    assert dialog.test_result.text().startswith("✓") and "API server proxy" in dialog.test_result.text()
    ws.shutdown()


def test_test_button_reports_a_missing_service_name(qtbot, tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    ws = ClusterWorkspace(ProxyFake(), "ctx", label_settings=LabelSettings(store))
    qtbot.addWidget(ws)
    dialog = PrometheusSourceDialog("", "proxy", 0, "", "", False, False)
    qtbot.addWidget(dialog)
    dialog.forward.setChecked(True)
    dialog.service.setText("")
    ws._test_source(dialog)
    assert dialog.test_result.text().startswith("✗") and "Name the service" in dialog.test_result.text()
    ws.shutdown()
