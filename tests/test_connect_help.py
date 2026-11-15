import subprocess
import sys

import pytest
from kubernetes import client, config
from kubernetes.config import exec_provider

from klotse.k8s import client as client_module, connect_help
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


def test_connect_reports_a_failed_helper_in_words(kubeconfig, monkeypatch):
    service = KubeService()
    service.kubeconfig = kubeconfig
    monkeypatch.setattr(connect_help, "missing_helper", lambda info: "")  # the helper runs here; it is the helper's own failure under test

    def boom(context, variables=None):
        raise config.ConfigException("exec: process returned 1. token expired")

    service._connect = boom
    with pytest.raises(ConnectionError) as error:
        service.connect("eks")
    assert "sign-in helper" in str(error.value) and "token expired" in str(error.value)


def test_connect_leaves_other_errors_alone(kubeconfig):
    service = KubeService()
    service.kubeconfig = kubeconfig

    def boom(context, variables=None):
        raise ValueError("odd")

    service._connect = boom
    with pytest.raises(ValueError):
        service.connect("plain")


def test_helper_stdin_guard_gives_the_helper_an_empty_stdin():
    assert connect_help.guard_helper_stdin() and connect_help.guard_helper_stdin()
    seen = exec_provider.subprocess.Popen([sys.executable, "-c", "import sys;print(repr(sys.stdin.read()))"],
                                          stdin=None, stdout=subprocess.PIPE, text=True)
    assert seen.communicate()[0].strip() == "''"


GKE_KUBECONFIG = """apiVersion: v1
kind: Config
current-context: gke
contexts:
- name: gke
  context: {cluster: gke, user: gke}
clusters:
- name: gke
  cluster: {server: "https://localhost:6550"}
users:
- name: gke
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      installHint: Install gke-gcloud-auth-plugin for use with kubectl by following
        https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin
      provideClusterInfo: true
"""


def test_auth_info_reads_the_installhint_of_the_exec_block(tmp_path):
    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG, encoding="utf-8")
    info = connect_help.auth_info(str(path), "gke")
    assert info["exec"] == "gke-gcloud-auth-plugin"
    assert info["install_hint"].startswith("Install gke-gcloud-auth-plugin for use with kubectl")
    assert "https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin" in info["install_hint"]


def test_a_missing_helper_shows_the_clusters_installhint(tmp_path):
    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG, encoding="utf-8")
    info = connect_help.auth_info(str(path), "gke")
    text = connect_help.explain(FileNotFoundError(2, "No such file or directory", "gke-gcloud-auth-plugin"), info)
    assert "was not found" in text and info["install_hint"] in text


def test_a_missing_helper_without_an_installhint_keeps_the_generic_advice(tmp_path):
    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG.replace("      installHint: Install", "      notHint: Install").replace(
        "        https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin\n", ""), encoding="utf-8")
    info = connect_help.auth_info(str(path), "gke")
    assert info["install_hint"] == ""
    text = connect_help.explain(FileNotFoundError(2, "No such file or directory", "gke-gcloud-auth-plugin"), info)
    assert "Install it or put it on the PATH" in text


def test_a_missing_helper_fails_before_any_request_with_its_installhint(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG.replace("command: gke-gcloud-auth-plugin", "command: klotse-no-such-helper-for-tests"), encoding="utf-8")
    monkeypatch.setattr(client_module.config, "load_kube_config", lambda **kwargs: None)
    monkeypatch.setattr(client_module.client.CoreV1Api, "get_api_resources", lambda self, **kwargs: pytest.fail("no request before the helper check"))
    with pytest.raises(ConnectionError, match="klotse-no-such-helper-for-tests\` was not found") as error:
        KubeService(str(path)).connect("gke")
    assert "Install gke-gcloud-auth-plugin for use with kubectl" in str(error.value)  # the cluster's own installHint is shown


def test_a_token_the_cluster_rejects_fails_the_connection_with_sign_in_advice(tmp_path, monkeypatch):
    from kubernetes.client.exceptions import ApiException

    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG, encoding="utf-8")
    monkeypatch.setattr(connect_help, "missing_helper", lambda info: "")  # the helper is installed where the test runs
    monkeypatch.setattr(client_module.config, "load_kube_config", lambda **kwargs: None)
    monkeypatch.setattr(client_module.client.CoreV1Api, "get_api_resources", lambda self, **kwargs: None)

    def rejected(self, body, **kwargs):
        raise ApiException(status=401, reason="Unauthorized")

    monkeypatch.setattr(client_module.client.AuthorizationV1Api, "create_self_subject_access_review", rejected)
    service = KubeService(str(path))
    with pytest.raises(ConnectionError, match="401 Unauthorized.*gke-gcloud-auth-plugin"):
        service.connect("gke")


def test_other_answers_to_the_access_review_do_not_fail_the_connection(tmp_path, monkeypatch):
    from kubernetes.client.exceptions import ApiException

    path = tmp_path / "config"
    path.write_text(GKE_KUBECONFIG, encoding="utf-8")
    monkeypatch.setattr(connect_help, "missing_helper", lambda info: "")
    monkeypatch.setattr(client_module.config, "load_kube_config", lambda **kwargs: None)
    monkeypatch.setattr(client_module.client.CoreV1Api, "get_api_resources", lambda self, **kwargs: None)

    def forbidden(self, body, **kwargs):
        raise ApiException(status=403, reason="Forbidden")

    monkeypatch.setattr(client_module.client.AuthorizationV1Api, "create_self_subject_access_review", forbidden)
    KubeService(str(path)).connect("gke")
