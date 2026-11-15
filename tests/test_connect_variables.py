"""Plugin variables (the AWS typed keys, profile, extra variables) reach the kubeconfig's sign-in helper only (issue #486)."""
import json
import os
import sys

from kubernetes import client

from klotse.k8s.client import KubeService

HELPER = """import json, os
print(json.dumps({"apiVersion": "client.authentication.k8s.io/v1beta1", "kind": "ExecCredential",
                  "status": {"token": os.environ.get("KLOTSE_TEST_KEY", "unset")}}))
"""


def write_kubeconfig(tmp_path, helper_path, exec_env=None):
    env = ""
    if exec_env:
        env = "      env:\n" + "".join(f"      - name: {k}\n        value: {v}\n" for k, v in exec_env.items())
    text = f"""apiVersion: v1
kind: Config
current-context: eks
contexts:
- name: eks
  context: {{cluster: c1, user: u1}}
- name: plain
  context: {{cluster: c2, user: u2}}
clusters:
- name: c1
  cluster: {{server: "https://eks.example.com"}}
- name: c2
  cluster: {{server: "https://plain.example.com"}}
users:
- name: u1
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: {sys.executable}
      args: [{helper_path}]
{env}- name: u2
  user:
    token: static
"""
    path = tmp_path / "kubeconfig"
    path.write_text(text, encoding="utf-8")
    return str(path)


def load(service, context, variables):
    cfg = client.Configuration()
    service._load_kube_config(context, cfg, variables)
    return cfg


def helper(tmp_path):
    path = tmp_path / "helper.py"
    path.write_text(HELPER, encoding="utf-8")
    return str(path)


def test_variables_reach_the_exec_helper_only(tmp_path, monkeypatch):
    monkeypatch.delenv("KLOTSE_TEST_KEY", raising=False)
    service = KubeService(kubeconfig=write_kubeconfig(tmp_path, helper(tmp_path)))
    cfg = load(service, "eks", {"KLOTSE_TEST_KEY": "typed-secret"})
    assert cfg.api_key["BearerToken"] == "Bearer typed-secret"
    assert "KLOTSE_TEST_KEY" not in os.environ  # the process environment is not changed
    text = open(service.kubeconfig, encoding="utf-8").read()
    assert "typed-secret" not in text  # nothing is written back to the kubeconfig


def test_without_variables_the_helper_sees_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("KLOTSE_TEST_KEY", raising=False)
    service = KubeService(kubeconfig=write_kubeconfig(tmp_path, helper(tmp_path)))
    cfg = load(service, "eks", {})
    assert cfg.api_key["BearerToken"] == "Bearer unset"


def test_variables_replace_a_kubeconfig_entry_of_the_same_name(tmp_path, monkeypatch):
    monkeypatch.delenv("KLOTSE_TEST_KEY", raising=False)
    service = KubeService(kubeconfig=write_kubeconfig(tmp_path, helper(tmp_path), {"KLOTSE_TEST_KEY": "from-file"}))
    cfg = load(service, "eks", {"KLOTSE_TEST_KEY": "typed"})
    assert cfg.api_key["BearerToken"] == "Bearer typed"


def test_context_without_exec_loads_as_before(tmp_path):
    service = KubeService(kubeconfig=write_kubeconfig(tmp_path, helper(tmp_path)))
    cfg = load(service, "plain", {"KLOTSE_TEST_KEY": "typed"})
    assert cfg.host == "https://plain.example.com"
    assert cfg.api_key.get("BearerToken") == "Bearer static"
