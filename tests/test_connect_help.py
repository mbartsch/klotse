import subprocess
import sys

import pytest
from kubernetes import client, config
from kubernetes.config import exec_provider

from klotse.k8s import connect_help
from klotse.k8s.client import KubeService

KUBECONFIG = """apiVersion: v1
kind: Config
current-context: eks
contexts:
- name: eks
  context: {cluster: c1, user: u1}
- name: plain
  context: {cluster: c2, user: u2}
clusters:
- name: c1
  cluster: {server: "https://eks.example.com", proxy-url: "http://proxy.example.com:3128"}
- name: c2
  cluster: {server: "https://plain.example.com"}
users:
- name: u1
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: C:\\tools\\aws.exe
      args: [eks, get-token, --cluster-name, x]
- name: u2
  user: {token: secret-token-value}
"""


@pytest.fixture
def kubeconfig(tmp_path):
    path = tmp_path / "config"
    path.write_text(KUBECONFIG, encoding="utf-8")
    return str(path)


def test_auth_info_reads_helper_proxy_and_server(kubeconfig):
    info = connect_help.auth_info(kubeconfig, "eks")
    assert info["exec"].endswith("aws.exe") and info["args"][:2] == ["eks", "get-token"]
    assert info["proxy"] == "http://proxy.example.com:3128" and info["server"] == "https://eks.example.com"
    plain = connect_help.auth_info(kubeconfig, "plain")
    assert plain["exec"] == "" and plain["proxy"] == "" and "secret-token-value" not in str(plain)


def test_auth_info_survives_missing_or_unknown(tmp_path, kubeconfig):
    assert connect_help.auth_info(str(tmp_path / "none"), "x")["exec"] == ""
    assert connect_help.auth_info(kubeconfig, "nope")["server"] == ""


def test_a_failing_helper_is_explained_with_the_fix(kubeconfig):
    info = connect_help.auth_info(kubeconfig, "eks")
    text = connect_help.explain("exec: process returned 255. The SSO session associated with this profile has expired", info)
    assert "failed (exit code 255)" in text and "expired" in text and "without a terminal" in text and "aws sso login" in text


def test_unknown_helper_gets_a_general_hint():
    text = connect_help.explain("exec: process returned 1", {"exec": "/usr/bin/my-login"})
    assert "my-login" in text and "terminal first" in text


def test_missing_helper_is_named():
    text = connect_help.explain(FileNotFoundError(2, "No such file or directory"), {"exec": "aws"})
    assert "`aws` was not found" in text and "PATH" in text


def test_unreachable_server_names_the_proxy(kubeconfig):
    text = connect_help.explain("Cannot reach the API server at https://eks.example.com", connect_help.auth_info(kubeconfig, "eks"))
    assert "uses the proxy http://proxy.example.com:3128 (from" in text and "proxy-url" in text
    assert "VPN" in connect_help.explain("Cannot reach the API server at x", {"server": "https://x"})


def test_other_errors_are_unchanged():
    assert connect_help.explain("ApiException: (500)", {"exec": "aws"}) == "ApiException: (500)"


def test_the_client_honours_proxy_url(kubeconfig):
    cfg = client.Configuration()
    config.load_kube_config(config_file=kubeconfig, context="eks", client_configuration=cfg)
    assert cfg.proxy == "http://proxy.example.com:3128"


def test_connect_reports_a_failed_helper_in_words(kubeconfig):
    service = KubeService()
    service.kubeconfig = kubeconfig

    def boom(context):
        raise config.ConfigException("exec: process returned 1. token expired")

    service._connect = boom
    with pytest.raises(ConnectionError) as error:
        service.connect("eks")
    assert "sign-in helper" in str(error.value) and "token expired" in str(error.value)


def test_connect_leaves_other_errors_alone(kubeconfig):
    service = KubeService()
    service.kubeconfig = kubeconfig

    def boom(context):
        raise ValueError("odd")

    service._connect = boom
    with pytest.raises(ValueError):
        service.connect("plain")


def test_helper_stdin_guard_gives_the_helper_an_empty_stdin():
    assert connect_help.guard_helper_stdin() and connect_help.guard_helper_stdin()
    seen = exec_provider.subprocess.Popen([sys.executable, "-c", "import sys;print(repr(sys.stdin.read()))"],
                                          stdin=None, stdout=subprocess.PIPE, text=True)
    assert seen.communicate()[0].strip() == "''"
