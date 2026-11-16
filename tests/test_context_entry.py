"""The kubeconfig entry shown before connecting: names, server and sign-in kind, never secret values."""
from klotse.k8s.context_entry import entry_lines

CONFIG = """
apiVersion: v1
kind: Config
contexts:
- name: prod-eu
  context: {cluster: eks-eu, user: eks-user, namespace: payments}
- name: lab
  context: {cluster: lab-cluster, user: lab-token}
clusters:
- name: eks-eu
  cluster: {server: 'https://eu.example.test', proxy-url: 'http://proxy.example.test:3128', certificate-authority-data: 'Q0FEQVRBLVNFQ1JFVA=='}
- name: lab-cluster
  cluster: {server: 'https://lab.example.test', insecure-skip-tls-verify: true}
users:
- name: eks-user
  user:
    exec: {command: aws, args: [eks, get-token, --cluster-name, eu]}
- name: lab-token
  user: {token: SUPER-SECRET-TOKEN, client-key-data: S0VZLVNFQ1JFVA==}
"""


def write(tmp_path):
    path = tmp_path / "config"
    path.write_text(CONFIG, encoding="utf-8")
    return str(path)


def test_an_exec_sign_in_and_embedded_ca_are_described_without_their_data(tmp_path):
    lines = entry_lines(write(tmp_path), "prod-eu")
    text = "\n".join(lines)
    assert "Server: https://eu.example.test" in text and "Namespace: payments" in text
    assert "Proxy: http://proxy.example.test:3128" in text
    assert "Certificate authority: embedded (not shown)" in text
    assert "Sign-in: exec helper: aws eks get-token --cluster-name eu" in text
    assert "Q0FEQVRBLVNFQ1JFVA" not in text


def test_a_token_is_reported_as_present_and_never_shown(tmp_path):
    text = "\n".join(entry_lines(write(tmp_path), "lab"))
    assert "bearer token: present (not shown)" in text and "client certificate: embedded (not shown)" in text
    assert "SUPER-SECRET-TOKEN" not in text and "S0VZLVNFQ1JFVA" not in text
    assert "not checked (insecure-skip-tls-verify is set)" in text
    assert "Namespace: default (none in the kubeconfig)" in text and "Proxy: none" in text


def test_an_unknown_context_and_a_missing_file_are_reported(tmp_path):
    assert entry_lines(write(tmp_path), "nope") == ["Context nope: not found in the kubeconfig."]
    assert entry_lines(str(tmp_path / "missing"), "prod-eu") == ["Context prod-eu: not found in the kubeconfig."]
