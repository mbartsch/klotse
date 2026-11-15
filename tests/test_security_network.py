"""Adversarial end-to-end metrics transport tests, using synthetic credentials and loopback only."""
import base64
from io import BytesIO
import ssl
import traceback
import urllib.error
import urllib.request

import pytest

from klotse.k8s import client, prometheus, process_env
from security_http import attack_servers  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.security
SECRET = "KLOTSE-SYNTHETIC-NETWORK-SENTINEL"


def source(url, monkeypatch, auth="bearer"):
    monkeypatch.setenv("KLOTSE_SECURITY_TOKEN", SECRET)
    ref = prometheus.parse_ref(url)
    return prometheus.with_auth(ref, auth, "KLOTSE_SECURITY_TOKEN", "test-user") if auth else ref


@pytest.mark.parametrize("auth", ["bearer", "basic", ""])
@pytest.mark.parametrize("tls", [False, True])
def test_direct_auth_and_same_origin_redirect_work(attack_servers, monkeypatch, tmp_path, auth, tls):
    origin, routes, seen = attack_servers(tls)
    routes["/start"] = (302, {"Location": "/final"}, b"")
    ref = source(origin, monkeypatch, auth)
    assert "success" in client.KubeService(str(tmp_path / "absent"))._direct_get(ref, "/start", 2)
    expected = ("Bearer " + SECRET if auth == "bearer" else
                "Basic " + base64.b64encode(("test-user:" + SECRET).encode()).decode() if auth else None)
    assert [headers.get("Authorization") for _, headers in seen] == [expected, expected]


@pytest.mark.parametrize("auth", ["bearer", "basic"])
@pytest.mark.parametrize("change", ["host", "port", "downgrade", "upgrade", "chain", "userinfo"])
def test_redirect_cannot_move_credentials(attack_servers, monkeypatch, tmp_path, caplog, auth, change):
    tls = change != "upgrade"
    origin, routes, seen = attack_servers(tls)
    other, _, stolen = attack_servers(change != "downgrade")
    destination = other + "/capture"
    if change == "host":
        destination = origin.replace("127.0.0.1", "localhost") + "/capture"
    if change == "userinfo":
        destination = origin.replace("://", "://user:" + SECRET + "@") + "/capture"
    if change == "chain":
        routes["/start"] = (302, {"Location": "/next"}, b"")
        routes["/next"] = (307, {"Location": destination}, b"")
    else:
        routes["/start"] = (302, {"Location": destination}, b"")
    service = client.KubeService(str(tmp_path / "absent"))
    with pytest.raises(prometheus.PrometheusError) as error:
        service._direct_get(source(origin, monkeypatch, auth), "/start", 2)
    assert stolen == [] and not any(path == "/capture" for path, _ in seen)
    assert SECRET not in str(error.value) + caplog.text


def test_anonymous_cross_origin_redirect_works_but_tls_downgrade_does_not(attack_servers, monkeypatch, tmp_path):
    origin, routes, _ = attack_servers()
    target, _, seen = attack_servers()
    routes["/start"] = (302, {"Location": target + "/final"}, b"")
    service = client.KubeService(str(tmp_path / "absent"))
    service._direct_get(source(origin, monkeypatch, ""), "/start", 2)
    assert seen[-1][1].get("Authorization") is None
    secure, redirects, _ = attack_servers(True)
    redirects["/start"] = (302, {"Location": target + "/forbidden"}, b"")
    with pytest.raises(prometheus.PrometheusError):
        service._direct_get(source(secure, monkeypatch, ""), "/start", 2)
    assert len(seen) == 1


@pytest.mark.parametrize("scheme", ["ftp://127.0.0.1/file", "file:///synthetic", "data:text/plain,synthetic"])
def test_redirect_rejects_other_protocols(attack_servers, monkeypatch, tmp_path, scheme):
    origin, routes, _ = attack_servers()
    routes["/start"] = (302, {"Location": scheme}, b"")
    with pytest.raises(prometheus.PrometheusError):
        client.KubeService(str(tmp_path / "absent"))._direct_get(source(origin, monkeypatch), "/start", 2)


def test_tls_verification_is_required(attack_servers, monkeypatch, tmp_path):
    origin, _, seen = attack_servers(True)
    attack_servers.trusted[0] = False
    with pytest.raises(prometheus.PrometheusError, match="URLError"):
        client.KubeService(str(tmp_path / "absent"))._direct_get(source(origin, monkeypatch), "/", 2)
    assert seen == []
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("fault", ["oversized", "malformed", "stall", "refused", "loop"])
def test_hostile_responses_fail_without_secrets(attack_servers, monkeypatch, tmp_path, fault, caplog):
    origin, routes, _ = attack_servers()
    monkeypatch.setattr(client, "MAX_PROMETHEUS_BODY", 32)
    routes["/start"] = {
        "oversized": (200, {}, b"x" * 100),
        "malformed": ("malformed", {}, b""),
        "stall": (200, {"X-Stall": "yes"}, b"x"),
        "refused": (401, {}, SECRET.encode()),
        "loop": (302, {"Location": "/start"}, b""),
    }[fault]
    with pytest.raises(prometheus.PrometheusError) as error:
        client.KubeService(str(tmp_path / "absent"))._direct_get(source(origin, monkeypatch), "/start", 0.15)
    assert SECRET not in str(error.value) + caplog.text


def test_origin_normalization_and_secret_bearing_transport_errors(monkeypatch, tmp_path):
    policy = client.MetricsRedirectPolicy()
    request = urllib.request.Request("https://METRICS.example/start", headers={"Authorization": "Bearer " + SECRET})
    moved = policy.redirect_request(request, None, 302, "Found", {}, "https://metrics.example:443/next")
    assert moved.get_header("Authorization") == "Bearer " + SECRET

    class Broken:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError("server-controlled " + SECRET)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Broken())
    with pytest.raises(prometheus.PrometheusError) as error:
        client.KubeService(str(tmp_path / "absent"))._direct_get(source("https://metrics.example", monkeypatch), "/", 1)
    assert SECRET not in str(error.value)


def test_http_error_tracebacks_do_not_include_the_server_reason_or_url(monkeypatch, tmp_path):
    class Broken:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError("https://metrics.example/" + SECRET, 401, SECRET, {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Broken())
    with pytest.raises(prometheus.PrometheusError) as error:
        client.KubeService(str(tmp_path / "absent"))._direct_get(source("https://metrics.example", monkeypatch), "/", 1)
    assert SECRET not in "".join(traceback.format_exception(error.type, error.value, error.tb))


def test_malformed_redirect_address_is_a_sanitized_error():
    request = urllib.request.Request("https://metrics.example", headers={"Authorization": "Bearer " + SECRET})
    with pytest.raises(prometheus.PrometheusError, match="invalid address"):
        client.MetricsRedirectPolicy().redirect_request(request, None, 302, "Found", {}, "https://[" + SECRET)


@pytest.mark.parametrize("destination", ["https://other.example/", "https://[broken", "https://metrics.example:invalid/"])
def test_rejected_redirect_closes_the_response(destination):
    response = BytesIO(b"synthetic")
    request = urllib.request.Request("https://metrics.example", headers={"Authorization": "Bearer " + SECRET})
    with pytest.raises(prometheus.PrometheusError):
        client.MetricsRedirectPolicy().redirect_request(request, response, 302, "Found", {}, destination)
    assert response.closed


def test_app_http_proxy_keeps_the_redirect_policy(attack_servers, monkeypatch, tmp_path):
    proxy, routes, seen = attack_servers()
    other, _, captured = attack_servers()
    routes["http://metrics.invalid/start"] = (302, {"Location": other + "/capture"}, b"")
    process_env.set_app_proxy(proxy)
    with pytest.raises(prometheus.PrometheusError):
        client.KubeService(str(tmp_path / "absent"))._direct_get(source("http://metrics.invalid", monkeypatch), "/start", 2)
    assert len(seen) == 1 and captured == []
