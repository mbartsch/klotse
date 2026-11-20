"""Teleport plugin (#377): Kubernetes clusters listed with `tsh kube ls` and added with `tsh kube login`, on the cluster-source
extension point. tsh is replaced by a fake that records the argv and environment (no Teleport, no network). The JSON shapes
below are assumptions about tsh's output, not captures of a real tsh."""
import json
from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import QSettings

from klotse.k8s import cloud_clusters as cloud
from klotse.plugins.api import CloudError, ClusterRef
from klotse.plugins.manager import BUNDLED_MODULES, PluginManager
from klotse.ui.cloud_clusters_dialog import CloudClustersDialog
from klotse_plugins.teleport import TeleportPlugin
from klotse_plugins.teleport import clusters as tp


class FakeTsh:
    """Records every argv and env; answers with `answer`, or raises it when it is an exception."""

    def __init__(self, answer=""):
        self.calls, self.envs, self.answer = [], [], answer

    def __call__(self, argv, env=None, timeout=None):
        self.calls.append(list(argv))
        self.envs.append(env)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer(argv, env) if callable(self.answer) else self.answer


LISTING = json.dumps([
    {"kube_cluster_name": "prod", "labels": {"env": "prod", "team": "a"}, "selected": True},
    {"kube_cluster_name": "staging", "labels": {}, "selected": False},
])


def test_listing_runs_tsh_kube_ls_and_reads_names_labels_and_selection():
    run = FakeTsh(LISTING)
    found = TeleportPlugin().list_clusters(tp.SOURCE, {}, run)
    assert run.calls == [["tsh", "kube", "ls", "--format=json"]]
    assert [(c.name, c.ref, c.group, c.state) for c in found] == [("prod", "prod", "env=prod, team=a", "selected"),
                                                                 ("staging", "staging", "", "")]


def test_listing_passes_the_proxy_and_a_full_tsh_path():
    run = FakeTsh(LISTING)
    found = TeleportPlugin().list_clusters(tp.SOURCE, {"proxy": "teleport.example.com:443",
                                                       "tsh": r"C:\Program Files\Teleport\tsh.exe"}, run)
    assert run.calls == [[r"C:\Program Files\Teleport\tsh.exe", "--proxy=teleport.example.com:443", "kube", "ls", "--format=json"]]
    assert found[0].location == "teleport.example.com:443"


@pytest.mark.parametrize("text", [
    json.dumps([{"name": "prod", "labels": {"env": "prod"}}]),
    json.dumps([{"kind": "kube_cluster", "metadata": {"name": "prod", "labels": {"env": "prod"}}}]),
    json.dumps({"kube_clusters": [{"kube_cluster_name": "prod", "static_labels": {"env": "prod"}}]}),
    "WARNING: something\n" + json.dumps([{"kube_cluster_name": "prod", "labels": {"env": "prod"}}]),
])
def test_listing_accepts_reasonable_alternative_shapes(text):
    found = tp.parse(text, "")
    assert [(c.name, c.group) for c in found] == [("prod", "env=prod")]


def test_the_teleport_cluster_name_is_shown_as_the_location():
    found = tp.parse(json.dumps([{"kube_cluster_name": "prod", "cluster": "root.example.com"}]), "proxy:443")
    assert found[0].location == "root.example.com"


@pytest.mark.parametrize("bad", ["-oProxyCommand=x", "--exec", "pro\nd", "pro\x1bd", "", "x" * 300])
def test_untrusted_names_are_left_out(bad):
    found = tp.parse(json.dumps([{"kube_cluster_name": bad}, {"kube_cluster_name": "ok"}]), "")
    assert [c.name for c in found] == ["ok"]


def test_only_untrusted_names_is_a_plain_error():
    with pytest.raises(CloudError, match="none has a name Klotse accepts"):
        tp.parse(json.dumps([{"kube_cluster_name": "--all"}]), "")


@pytest.mark.parametrize("text", ["[]", "", "null", json.dumps({"kube_clusters": []})])
def test_no_clusters_is_a_plain_error(text):
    with pytest.raises(CloudError, match="no Kubernetes clusters"):
        tp.parse(text, "")


@pytest.mark.parametrize("text", ["not json", json.dumps({"something": 1})])
def test_unexpected_output_is_refused(text):
    with pytest.raises(CloudError):
        tp.parse(text, "")


def test_tsh_not_installed_is_said_in_plain_words():
    run = FakeTsh(CloudError("the tsh command is not installed or not on the PATH"))
    with pytest.raises(CloudError, match="tsh \\(Teleport's command-line client\\) is not installed"):
        TeleportPlugin().list_clusters(tp.SOURCE, {}, run)


@pytest.mark.parametrize("stderr", ["ERROR: Not logged in.", "ERROR: ssh: cert has expired", "your session has expired"])
def test_not_signed_in_says_to_run_tsh_login_in_a_terminal(stderr):
    run = FakeTsh(CloudError(f"tsh failed: {stderr}"))
    with pytest.raises(CloudError) as raised:
        TeleportPlugin().list_clusters(tp.SOURCE, {"proxy": "teleport.example.com"}, run)
    assert "tsh login --proxy=teleport.example.com" in str(raised.value) and "terminal" in str(raised.value)


def test_other_failures_keep_tsh_reason():
    run = FakeTsh(CloudError("tsh failed: access denied to kube cluster"))
    with pytest.raises(CloudError, match="access denied"):
        TeleportPlugin().list_clusters(tp.SOURCE, {}, run)


@pytest.mark.parametrize("scope", [{"proxy": "https://teleport.example.com"}, {"proxy": "-x"}, {"proxy": "a b"},
                                   {"tsh": "powershell.exe"}, {"tsh": "-tsh"}, {"tsh": "C:\\bin\x07\\tsh.exe"}, {"tsh": "/bin/sh"}])
def test_unsafe_scope_is_refused_before_running_anything(scope):
    run = FakeTsh(LISTING)
    with pytest.raises(CloudError):
        TeleportPlugin().list_clusters(tp.SOURCE, scope, run)
    assert run.calls == []


def test_credentials_run_tsh_kube_login_with_kubeconfig_set_to_the_target():
    run = FakeTsh("")
    TeleportPlugin().cluster_credentials(tp.SOURCE, ClusterRef("prod", "prod"), {"proxy": "teleport.example.com:443"},
                                         Path("piece.yaml"), run)
    assert run.calls == [["tsh", "--proxy=teleport.example.com:443", "kube", "login", "prod"]]
    assert run.envs == [{"KUBECONFIG": "piece.yaml"}]


@pytest.mark.parametrize("bad", ["-x", "--all", "a\x00b", ""])
def test_credentials_refuse_an_untrusted_name(bad):
    run = FakeTsh("")
    with pytest.raises(CloudError):
        TeleportPlugin().cluster_credentials(tp.SOURCE, ClusterRef(bad, bad), {}, Path("piece.yaml"), run)
    assert run.calls == []


def test_defaults_take_the_proxy_from_teleport_proxy():
    assert TeleportPlugin().cluster_defaults(tp.SOURCE, {"TELEPORT_PROXY": "tp.example.com:443"}) == {"proxy": "tp.example.com:443"}
    assert TeleportPlugin().cluster_defaults(tp.SOURCE, {}) == {}


def test_the_login_merges_into_the_target_without_changing_its_current_context(tmp_path, monkeypatch):
    """The fake tsh writes a kubeconfig and switches current-context in the file KUBECONFIG names, as tsh kube login does;
    the dialog's add step merges it and the target keeps its own current-context."""
    target = tmp_path / "klotse-cloud.klotse"
    target.write_text(yaml.safe_dump({"apiVersion": "v1", "kind": "Config", "current-context": "mine",
                                      "contexts": [{"name": "mine", "context": {"cluster": "mine", "user": "mine"}}],
                                      "clusters": [{"name": "mine", "cluster": {"server": "https://mine"}}],
                                      "users": [{"name": "mine", "user": {}}]}), encoding="utf-8")

    def login(argv, env):
        name = argv[-1]
        Path(env["KUBECONFIG"]).write_text(yaml.safe_dump({
            "apiVersion": "v1", "kind": "Config", "current-context": f"root-{name}",
            "clusters": [{"name": "root", "cluster": {"server": "https://root:443"}}],
            "contexts": [{"name": f"root-{name}", "context": {"cluster": "root", "user": f"root-{name}"}}],
            "users": [{"name": f"root-{name}", "user": {"exec": {"command": "tsh"}}}]}), encoding="utf-8")
        return ""

    monkeypatch.setattr(cloud, "run", FakeTsh(login))
    added, skipped, errors = CloudClustersDialog._add_worker(TeleportPlugin(), tp.SOURCE, {}, [ClusterRef("prod", "prod")],
                                                             target)
    assert (added, skipped, errors) == (["root-prod"], [], [])
    merged = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert merged["current-context"] == "mine"
    assert {c["name"] for c in merged["contexts"]} == {"mine", "root-prod"}


def test_it_is_a_bundled_plugin_that_loads_and_is_off_until_enabled(qtbot, tmp_path):
    assert "klotse_plugins.teleport" in BUNDLED_MODULES
    manager = PluginManager(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat), entry_points=False)
    found = next(f for f in manager.loaded if f.id == "teleport")
    assert found.source == "bundled" and found.error == "" and not manager.is_enabled("teleport")
    assert manager.cluster_sources() == []
    manager.set_enabled("teleport", True)
    qtbot.waitUntil(lambda: manager.is_enabled("teleport"), timeout=20000)
    assert [source.key for _plugin, source in manager.cluster_sources()] == ["teleport"]
