"""URL metrics sources: parsing, the saved authentication (names of environment variables, never secrets), the direct request with its
authentication headers, and the error messages. The request goes to a local HTTP server started by the test; nothing leaves the machine."""
import base64
import http.server
import threading

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s import prometheus, process_env
from klotse.k8s.client import KubeService
from klotse.ui.metrics_sources import MetricsSources


def sources(tmp_path):
    return MetricsSources(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat))


# --- parsing -----------------------------------------------------------------------------------------------------------

def test_a_url_source_keeps_its_origin_and_path_prefix():
    ref = prometheus.parse_ref("https://vm.example.com:8443/select/0/prometheus/")
    assert ref["origin"] == "https://vm.example.com:8443" and ref["prefix"] == "/select/0/prometheus"
    assert prometheus.ref_text(ref) == "https://vm.example.com:8443/select/0/prometheus"
    assert prometheus.reach_path(ref, "/api/v1/query") == "/select/0/prometheus/api/v1/query"


def test_a_url_defaults_its_port_from_the_scheme():
    assert prometheus.parse_ref("http://vm.example.com/")["port"] == 80
    assert prometheus.parse_ref("https://vm.example.com")["port"] == 443


@pytest.mark.parametrize("text", ["https://user:secret@vm.example.com", "https://vm.example.com/x?y=1", "https://", "https://vm.example.com:99999"])
def test_a_url_with_credentials_a_query_or_a_bad_port_is_refused(text):
    with pytest.raises(ValueError):
        prometheus.parse_ref(text)


def test_auth_applies_only_to_a_url_source():
    service = prometheus.parse_ref("monitoring/vmsingle:8428")
    assert prometheus.with_auth(service, "bearer", "TOKEN_VAR") is service
    url = prometheus.with_auth(prometheus.parse_ref("https://vm.example.com"), "basic", "PASS_VAR", "reader")
    assert url["auth"] == "basic" and url["auth_env"] == "PASS_VAR" and url["auth_user"] == "reader"


# --- settings: names only, never the secret ----------------------------------------------------------------------------

def test_a_url_source_saves_its_auth_names_and_no_secret(tmp_path):
    s = sources(tmp_path)
    s.set("prod", "https://vm.example.com/select/0/prometheus", auth="bearer", auth_env="METRICS_TOKEN")
    entry = s.own("prod")
    assert entry["ref"] == "https://vm.example.com/select/0/prometheus"
    assert entry["auth"] == "bearer" and entry["auth_env"] == "METRICS_TOKEN"
    text = open(s.store.fileName(), encoding="utf-8").read()
    assert "METRICS_TOKEN" in text and "secret" not in text.lower()


def test_auth_is_dropped_for_a_service_source(tmp_path):
    s = sources(tmp_path)
    s.set("prod", "monitoring/vmsingle:8428", auth="bearer", auth_env="METRICS_TOKEN")
    assert s.own("prod")["auth"] == ""


def test_a_forward_is_never_kept_for_a_url(tmp_path):
    s = sources(tmp_path)
    s.set("prod", "https://vm.example.com", via="forward", local=9090)
    assert s.get_via("prod") == "proxy"


def test_a_cluster_without_auth_falls_back_to_the_default_auth(tmp_path):
    s = sources(tmp_path)
    s.set_default("https://vm.example.com", auth="bearer", auth_env="DEFAULT_TOKEN")
    assert s.get_auth("prod") == ("bearer", "DEFAULT_TOKEN", "")
    s.set("prod", "https://other.example.com", auth="basic", auth_env="OTHER_PASS", auth_user="reader")
    assert s.get_auth("prod") == ("basic", "OTHER_PASS", "reader")


# --- the request ---------------------------------------------------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):  # noqa: N802 - the http.server name
        _Handler.seen.append(self.headers.get("Authorization"))
        if self.path.startswith("/refuse"):
            self.send_response(401)
            self.end_headers()
            return
        body = b'{"status":"success","data":{"result":[]}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server(monkeypatch):
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(variable, raising=False)
    process_env.set_app_proxy(None)
    _Handler.seen = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _ref(port, **auth):
    return prometheus.with_auth(prometheus.parse_ref(f"http://127.0.0.1:{port}/select/0/prometheus"), **auth) if auth else \
        prometheus.parse_ref(f"http://127.0.0.1:{port}/select/0/prometheus")


def test_a_bearer_token_is_sent_from_the_named_variable(server, tmp_path, monkeypatch):
    monkeypatch.setenv("KLOTSE_TEST_TOKEN", "abc123")
    service = KubeService(str(tmp_path / "missing-kubeconfig"))
    ref = _ref(server, auth="bearer", auth_env="KLOTSE_TEST_TOKEN")
    service._direct_get(ref, prometheus.instant_path(ref, "up"), 5)
    assert _Handler.seen[-1] == "Bearer abc123"


def test_basic_auth_sends_user_and_password_from_the_named_variable(server, tmp_path, monkeypatch):
    monkeypatch.setenv("KLOTSE_TEST_PASS", "pw-1")
    service = KubeService(str(tmp_path / "missing-kubeconfig"))
    ref = _ref(server, auth="basic", auth_env="KLOTSE_TEST_PASS", auth_user="reader")
    service._direct_get(ref, prometheus.instant_path(ref, "up"), 5)
    expected = "Basic " + base64.b64encode(b"reader:pw-1").decode()
    assert _Handler.seen[-1] == expected


def test_no_auth_sends_no_authorization_header(server, tmp_path):
    service = KubeService(str(tmp_path / "missing-kubeconfig"))
    service._direct_get(_ref(server), "/select/0/prometheus/api/v1/query?query=up", 5)
    assert _Handler.seen[-1] is None


def test_a_missing_variable_is_reported_by_name_without_sending_anything(server, tmp_path, monkeypatch):
    monkeypatch.delenv("KLOTSE_TEST_ABSENT", raising=False)
    service = KubeService(str(tmp_path / "missing-kubeconfig"))
    ref = _ref(server, auth="bearer", auth_env="KLOTSE_TEST_ABSENT")
    with pytest.raises(prometheus.PrometheusError, match="KLOTSE_TEST_ABSENT is not set"):
        service._direct_get(ref, "/select/0/prometheus/api/v1/query?query=up", 5)
    assert _Handler.seen == []


def test_a_refused_request_says_the_status(server, tmp_path):
    service = KubeService(str(tmp_path / "missing-kubeconfig"))
    with pytest.raises(prometheus.PrometheusError, match="HTTP 401"):
        service._direct_get(_ref(server), "/refuse", 5)


def test_a_socks_app_proxy_is_refused_for_a_url_source(server, tmp_path):
    process_env.set_app_proxy("socks5://proxy.corp:1080")
    try:
        service = KubeService(str(tmp_path / "missing-kubeconfig"))
        with pytest.raises(prometheus.PrometheusError, match="SOCKS"):
            service._direct_get(_ref(server), "/x", 5)
    finally:
        process_env.set_app_proxy(None)
