"""#795: a GKE emulator on this computer (Floci) gets a test token instead of gke-gcloud-auth-plugin, keeps its CA, and a failing
sign-in helper is reported instead of the client's 'NoneType' object has no attribute 'strip' and a later TLS error."""
import subprocess
import sys

import pytest
import yaml
from kubernetes import config
from kubernetes.config import exec_provider
from kubernetes.config.kube_config import ConfigNode

from klotse.k8s import connect_help
from klotse.k8s.client import KubeService
from klotse.plugins.api import ClusterRef, CloudError
from klotse_plugins.gcp import clusters

CA = "LS0tLS1CRUdJTi1DQS0tLS0t"


def gcloud_kubeconfig(server="https://localhost:6550", ca=CA):
    cluster = {"server": server}
    if ca:
        cluster["certificate-authority-data"] = ca
    return {"apiVersion": "v1", "kind": "Config", "current-context": "gke_p_l_c",
            "clusters": [{"name": "gke_p_l_c", "cluster": cluster}],
            "contexts": [{"name": "gke_p_l_c", "context": {"cluster": "gke_p_l_c", "user": "gke_p_l_c"}}],
            "users": [{"name": "gke_p_l_c", "user": {"exec": {"apiVersion": "client.authentication.k8s.io/v1beta1",
                                                             "command": "gke-gcloud-auth-plugin", "provideClusterInfo": True}}}]}


def fake_run(server="https://localhost:6550", ca=CA):
    def run(argv, env=None):
        if "get-credentials" in argv:
            with open(env["KUBECONFIG"], "w", encoding="utf-8") as handle:
                yaml.safe_dump(gcloud_kubeconfig(server, ca), handle)
        return ""
    return run


def written(target):
    return yaml.safe_load(target.read_text(encoding="utf-8"))


@pytest.mark.parametrize("value, local", [
    ("http://container.localhost:4588/", True), ("http://localhost:4588/", True), ("http://127.0.0.1:4588/", True),
    ("http://[::1]:4588/", True), ("", False), ("https://container.googleapis.com/", False),
    ("http://localhost.example.com/", False), ("http://[bad", False),
])
def test_only_a_container_endpoint_on_this_computer_is_an_emulator(value, local):
    assert clusters.local_emulator({clusters.ENDPOINT_VARIABLE: value}) is local


def test_emulator_import_uses_a_test_token_and_keeps_the_ca(tmp_path, monkeypatch):
    monkeypatch.setenv(clusters.ENDPOINT_VARIABLE, "http://container.localhost:4588/")
    target = tmp_path / "piece.yaml"
    clusters.cluster_credentials(ClusterRef("c", "c", "l"), {"project": "p"}, target, fake_run())
    data = written(target)
    assert data["users"][0]["user"] == {"token": clusters.EMULATOR_TOKEN}
    assert data["clusters"][0]["cluster"] == {"server": "https://localhost:6550", "certificate-authority-data": CA}
    assert "insecure-skip-tls-verify" not in str(data)


def test_real_gcp_import_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(clusters.ENDPOINT_VARIABLE, raising=False)
    target = tmp_path / "piece.yaml"
    clusters.cluster_credentials(ClusterRef("c", "c", "l"), {"project": "p"}, target, fake_run("https://34.1.2.3"))
    assert written(target) == gcloud_kubeconfig("https://34.1.2.3")


def test_emulator_token_never_goes_to_a_server_off_this_computer(tmp_path, monkeypatch):
    monkeypatch.setenv(clusters.ENDPOINT_VARIABLE, "http://127.0.0.1:4588/")
    target = tmp_path / "piece.yaml"
    clusters.cluster_credentials(ClusterRef("c", "c", "l"), {"project": "p"}, target, fake_run("https://10.0.0.5:6443"))
    assert written(target)["users"][0]["user"]["exec"]["command"] == "gke-gcloud-auth-plugin"


def test_emulator_cluster_without_a_ca_is_refused_not_made_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv(clusters.ENDPOINT_VARIABLE, "http://localhost:4588/")
    target = tmp_path / "piece.yaml"
    with pytest.raises(CloudError, match="no CA certificate"):
        clusters.cluster_credentials(ClusterRef("c", "c", "l"), {"project": "p"}, target, fake_run(ca=""))
    assert "token" not in target.read_text(encoding="utf-8")


def test_unreadable_gcloud_output_is_a_cloud_error(tmp_path):
    target = tmp_path / "piece.yaml"
    target.write_text("[unclosed", encoding="utf-8")
    with pytest.raises(CloudError):
        clusters.use_emulator_token(target)


def helper(code, stderr=""):
    script = f"import sys; sys.stderr.write({stderr!r}); sys.exit({code})"
    return {"apiVersion": "client.authentication.k8s.io/v1beta1", "command": sys.executable, "args": ["-c", script]}


@pytest.fixture
def failing_helper(tmp_path):
    path = tmp_path / "config"
    path.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config", "current-context": "gke",
        "contexts": [{"name": "gke", "context": {"cluster": "gke", "user": "gke"}}],
        "clusters": [{"name": "gke", "cluster": {"server": "https://127.0.0.1:1"}}],
        "users": [{"name": "gke", "user": {"exec": helper(1, "gcloud config config-helper failed: no active account")}}]}),
        encoding="utf-8")
    return str(path)


def test_a_failing_helper_fails_the_connection_with_its_message(failing_helper, monkeypatch):
    service = KubeService()
    service.kubeconfig = failing_helper
    monkeypatch.setattr(connect_help, "missing_helper", lambda info: "")
    with pytest.raises(ConnectionError) as error:
        service.connect("gke")
    text = str(error.value)
    assert "failed (exit code 1)" in text and "no active account" in text
    assert "strip" not in text and "CERTIFICATE" not in text and "Cannot reach" not in text


def test_helper_stderr_on_a_terminal_does_not_become_none_strip(monkeypatch):
    """The client passes stderr=sys.stderr when stdout is a terminal; communicate() then returns None for stderr."""
    connect_help.guard_helper_stdin()
    process = exec_provider.subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"], stdout=subprocess.PIPE,
                                             stderr=None, universal_newlines=True)
    out, err = process.communicate()
    assert err == "" and process.wait() == 1


def test_terminal_helper_failure_is_the_exit_code_not_an_attribute_error(monkeypatch):
    connect_help.guard_helper_stdin()
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(exec_provider.sys, "stdin", None)
    provider = exec_provider.ExecProvider(ConfigNode("exec", helper(3)), None)
    with pytest.raises(config.ConfigException, match="exec: process returned 3"):
        provider.run()
    text = connect_help.explain("exec: process returned 3", {"exec": "gke-gcloud-auth-plugin"})
    assert "no message Klotse could read" in text and "gcloud auth login" in text


def test_helper_failures_are_only_collected_inside_the_block():
    with connect_help.helper_failures() as failures:
        provider = exec_provider.ExecProvider(ConfigNode("exec", helper(2)), None)
        with pytest.raises(config.ConfigException):
            provider.run()
    assert len(failures) == 1 and "returned 2" in str(failures[0])
    with pytest.raises(config.ConfigException):
        provider.run()
    assert len(failures) == 1
