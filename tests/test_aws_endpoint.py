import pytest

from klotse.plugins.api import ActionRequest
from klotse_plugins.aws import native, identity, sso_login_argv
from klotse_plugins.aws.safe import endpoint_override, require_endpoint
from klotse_plugins.aws.sigv4 import sign


@pytest.mark.parametrize("url", ["http://127.0.0.1:4566/", "http://localhost:4566/", "http://[::1]:4566/", "https://ec2.example.com/"])
def test_endpoint_allows_https_anywhere_and_http_only_to_this_computer(url):
    assert require_endpoint(url) == url


@pytest.mark.parametrize("url", ["http://ec2.us-east-1.amazonaws.com/", "http://10.0.0.5:4566/", "ftp://127.0.0.1/", "file:///etc/passwd"])
def test_endpoint_refuses_other_hosts_over_http_and_other_schemes(url):
    with pytest.raises(ValueError):
        require_endpoint(url)


def test_endpoint_override_reads_aws_endpoint_url_and_adds_a_slash():
    assert endpoint_override({"AWS_ENDPOINT_URL": "http://127.0.0.1:4566"}) == "http://127.0.0.1:4566/"
    assert endpoint_override({"AWS_ENDPOINT_URL": "  "}) is None
    assert endpoint_override({}) is None


def test_ec2_and_sts_use_the_override_with_http(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:4566")
    assert native.endpoint("eu-west-1") == "http://127.0.0.1:4566/"
    assert identity._endpoint("eu-west-1") == "http://127.0.0.1:4566/"


def test_without_the_override_the_regional_https_endpoints_are_used(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    assert native.endpoint("eu-west-1") == "https://ec2.eu-west-1.amazonaws.com/"
    assert identity._endpoint("cn-north-1") == "https://sts.cn-north-1.amazonaws.com.cn/"


def test_signature_covers_the_port_of_a_local_endpoint():
    args = ("POST", {}, b"Action=X", "AK", "SK", "us-east-1", "ec2", "20260101T000000Z")
    with_port = sign(args[0], "http://127.0.0.1:4566/", *args[1:])["Authorization"]
    other_port = sign(args[0], "http://127.0.0.1:4567/", *args[1:])["Authorization"]
    assert with_port != other_port


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_insecure_net_lets_plain_http_reach_any_host(value):
    url = "http://ec2.eu-west-1.amazonaws.com/"
    assert require_endpoint(url, {"KLOTSE_INSECURE_NET": value}) == url


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_insecure_net_is_off_unless_it_is_true(value):
    with pytest.raises(ValueError):
        require_endpoint("http://ec2.eu-west-1.amazonaws.com/", {"KLOTSE_INSECURE_NET": value})


def test_insecure_net_still_refuses_other_schemes():
    for url in ("ftp://ec2.example.com/", "file:///etc/passwd", "gopher://10.0.0.1/"):
        with pytest.raises(ValueError):
            require_endpoint(url, {"KLOTSE_INSECURE_NET": "true"})


def test_override_to_a_remote_http_host_needs_the_switch(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://floci.lab.internal:4566")
    monkeypatch.delenv("KLOTSE_INSECURE_NET", raising=False)
    with pytest.raises(ValueError):
        endpoint_override()
    monkeypatch.setenv("KLOTSE_INSECURE_NET", "true")
    assert endpoint_override() == "http://floci.lab.internal:4566/"
    assert native.endpoint("eu-west-1") == "http://floci.lab.internal:4566/"
