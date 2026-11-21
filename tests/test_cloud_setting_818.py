"""#818: one Cloud setting per cluster, owned by Klotse and detected from the kubeconfig, instead of a "Cloud of this
cluster" box in each cloud plugin. No cloud CLI, no network: kubeconfigs are written to temporary files."""
import pytest
import yaml
from PySide6.QtWidgets import QMessageBox

from klotse.k8s import cloud_detect
from klotse.plugins import ActionRequest, Message, ResourceInfo, Terminal
from klotse.ui.cloud_settings_dialog import CloudSettingsDialog
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.azure import AzurePlugin
from klotse_plugins.gcp import GcpPlugin
from klotse_plugins.oci import OciPlugin
from test_app import FakeService, open_active
from test_cluster_settings import Zoned
from test_plugins import manager_with, store

AWS_NODE = {"metadata": {"name": "n1"}, "spec": {"providerID": "aws:///eu-west-1a/i-0abc"}}


# ---- detection from the kubeconfig ------------------------------------------------------------------------------------
@pytest.mark.parametrize("command, args, cloud", [
    ("aws", ["--region", "eu-west-1", "eks", "get-token", "--cluster-name", "prod"], "aws"),
    (r"C:\Program Files\Amazon\AWSCLIV2\aws.exe", ["eks", "get-token"], "aws"),
    ("aws-iam-authenticator", ["token", "-i", "prod"], "aws"),
    ("gke-gcloud-auth-plugin", [], "gcp"),
    ("/usr/lib/google-cloud-sdk/bin/gcloud", ["config", "config-helper"], "gcp"),
    ("kubelogin", ["get-token", "--login", "azurecli"], "azure"),
    ("oci", ["ce", "cluster", "generate-token", "--cluster-id", "ocid1.cluster.oc1..x"], "oci"),
    ("aws", ["sts", "get-caller-identity"], ""),  # aws, but not an EKS token
    ("oci", ["iam", "region", "list"], ""),
    ("kubectl-oidc_login", ["get-token"], ""),
    ("", [], ""),
])
def test_the_sign_in_helper_names_the_cloud(command, args, cloud):
    assert cloud_detect.from_helper(command, args) == cloud


@pytest.mark.parametrize("server, cloud", [
    ("https://ABCDEF.gr7.eu-west-1.eks.amazonaws.com", "aws"),
    ("https://prod-dns-1a2b.hcp.westeurope.azmk8s.io:443", "azure"),
    ("https://x.containerengine.us-ashburn-1.oraclecloud.com:6443", "oci"),
    ("https://34.120.0.1", ""),  # GKE answers on an address: the helper says it instead
    ("https://localhost:16443", ""),
    ("https://evil.example/eks.amazonaws.com", ""),  # the path does not count
    ("not a url", ""),
])
def test_the_server_host_names_the_cloud(server, cloud):
    assert cloud_detect.from_server(server) == cloud


def test_detect_prefers_the_helper_and_says_why():
    assert cloud_detect.detect({"exec": "kubelogin", "args": [], "server": "https://x.eks.amazonaws.com"}) == (
        "azure", "from the kubeconfig's sign-in helper (kubelogin)")
    assert cloud_detect.detect({"exec": "", "server": "https://x.azmk8s.io"}) == ("azure", "from the API server address")
    assert cloud_detect.detect({}) == ("", "")
    assert cloud_detect.effective("auto", "aws") == "aws" and cloud_detect.effective("gcp", "aws") == "gcp"
    assert cloud_detect.effective("", "") == "" and cloud_detect.effective("bogus", "oci") == "oci"


# ---- the one Cloud setting in the plugin manager --------------------------------------------------------------------
def test_the_cloud_is_saved_once_and_reaches_every_cloud_plugin_but_no_other(tmp_path):
    manager = manager_with(tmp_path, AwsPlugin(), GcpPlugin(), Zoned())
    changed = []
    manager.settings_changed.connect(changed.append)
    assert manager.cluster_cloud("prod") == "auto"
    assert manager.settings_for("gcp", "prod")["cloud"] == "auto"
    manager.set_cluster_cloud("prod", "gcp")
    assert manager.cluster_cloud("prod") == "gcp" and manager.cluster_cloud("other") == "auto"
    assert manager.settings_for("gcp", "prod")["cloud"] == "gcp" and manager.settings_for("aws", "prod")["cloud"] == "gcp"
    assert "cloud" not in manager.settings_for("zoned", "prod")
    assert sorted(changed) == ["aws", "gcp"]  # the cloud plugins ask again
    assert [p.id for p in manager.cloud_plugins()] == ["aws", "gcp"]
    manager.set_cluster_cloud("prod", "auto")
    assert manager.cluster_cloud("prod") == "auto"


def test_a_cloud_saved_by_a_plugin_before_818_is_read_and_moved_on_the_next_save(tmp_path):
    manager = manager_with(tmp_path, GcpPlugin(), AzurePlugin(), Zoned())
    manager.store.setValue("plugin_gcp_clusters/context.0", "lab-gke")
    manager.store.setValue("plugin_gcp_clusters/cloud.0", "gcp")
    manager.store.setValue("plugin_azure_clusters/context.0", "lab-gke")
    manager.store.setValue("plugin_azure_clusters/cloud.0", "auto")  # another plugin's auto does not hide it
    manager.store.setValue("plugin_azure_clusters/context.1", "odd")
    manager.store.setValue("plugin_azure_clusters/cloud.1", "gcp")  # not Azure's own id: ignored
    manager.set_cluster_settings("zoned", "lab-gke", {"team": "blue"})
    assert manager.cluster_cloud("lab-gke") == "gcp" and manager.cluster_cloud("odd") == "auto"
    assert manager.settings_for("gcp", "lab-gke")["cloud"] == "gcp"
    manager.set_cluster_cloud("lab-gke", "auto")
    assert manager.cluster_cloud("lab-gke") == "auto"  # the old value went with the save
    assert "lab-gke" not in manager._read_numbered("plugin_gcp_clusters")
    assert manager.cluster_settings_for("zoned", "lab-gke") == {"team": "blue"}  # other plugins' values stay


@pytest.mark.parametrize("plugin, cloud, accepted", [
    (GcpPlugin(), "gcp", Terminal(("gcloud", "auth", "login"))),
    (AzurePlugin(), "azure", Terminal(("az", "login"))),
])
def test_a_plugin_accepts_a_node_without_its_providerid_once_the_cluster_cloud_is_set(tmp_path, plugin, cloud, accepted):
    manager = manager_with(tmp_path, plugin)

    def run():
        settings = manager.settings_for(plugin.id, "ctx")
        return plugin.run(ActionRequest(plugin.id, "sign-in", "Node", "ctx", [ResourceInfo("n1", "", AWS_NODE)], settings))

    assert isinstance(run(), Message)  # auto: the providerID check refuses an AWS node
    manager.set_cluster_cloud("ctx", cloud)
    assert run() == accepted


def test_the_three_plugins_no_longer_ask_for_the_cloud_themselves():
    for cls in (GcpPlugin, AzurePlugin, OciPlugin):
        assert cls.cluster_settings == () and cls.cloud == cls.id and cls.requires_api == 14
    assert AwsPlugin.cloud == "aws" and [s.key for s in AwsPlugin.cluster_settings] == ["cluster_region", "cluster_profile",
                                                                                      "cluster_account"]


# ---- the dialog ---------------------------------------------------------------------------------------------------
def test_the_dialog_shows_only_the_chosen_clouds_settings(qtbot, tmp_path):
    manager = manager_with(tmp_path, AwsPlugin(), GcpPlugin(), OciPlugin())
    dialog = CloudSettingsDialog(manager, "prod", ("aws", "from the kubeconfig's sign-in helper (aws)"))
    qtbot.addWidget(dialog)
    texts = [dialog.choice.itemText(i) for i in range(dialog.choice.count())]
    assert texts == ["Auto: AWS, from the kubeconfig's sign-in helper (aws)", "AWS", "Google Cloud", "Oracle Cloud"]
    assert dialog.cloud() == "aws" and set(dialog.values()) == {"cluster_region", "cluster_profile", "cluster_account"}
    dialog.fields["aws"]["cluster_region"].setText("eu-west-1")
    dialog.choice.setCurrentIndex(dialog.choice.findData("gcp"))
    assert dialog.cloud() == "gcp" and dialog.values() == {}  # Google Cloud has nothing else for one cluster
    assert dialog.current_page is dialog._page["gcp"] and dialog._page["aws"].isHidden()  # hidden: takes no room
    dialog.choice.setCurrentIndex(dialog.choice.findData("aws"))
    assert dialog.values()["cluster_region"] == "eu-west-1"  # typed text stays when switching back
    dialog.save()
    assert manager.cluster_cloud("prod") == "aws"
    assert manager.cluster_settings_for("aws", "prod") == {"cluster_region": "eu-west-1"}


def test_not_detected_shows_a_note_and_a_bad_value_is_refused(qtbot, tmp_path, monkeypatch):
    manager = manager_with(tmp_path, AwsPlugin())
    dialog = CloudSettingsDialog(manager, "kind-local", ("", ""))
    qtbot.addWidget(dialog)
    assert dialog.choice.itemText(0) == "Auto: not detected from the kubeconfig"
    assert dialog.cloud() == "" and dialog.current_page is dialog.none_page
    dialog.choice.setCurrentIndex(dialog.choice.findData("aws"))
    dialog.fields["aws"]["cluster_account"].setText("12")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dialog.save()
    assert warned and "AWS account ID" in warned[0] and manager.cluster_cloud("kind-local") == "auto"  # nothing saved


# ---- the cluster menu ---------------------------------------------------------------------------------------------
def test_the_cluster_menu_has_one_cloud_entry_named_after_the_detected_cloud(qtbot, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(yaml.safe_dump({
        "contexts": [{"name": "ctx", "context": {"cluster": "c", "user": "u"}}],
        "clusters": [{"name": "c", "cluster": {"server": "https://34.120.0.1"}}],
        "users": [{"name": "u", "user": {"exec": {"command": "gke-gcloud-auth-plugin"}}}]}), encoding="utf-8")
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    win.plugins.loaded = manager_with(tmp_path, AwsPlugin(), GcpPlugin(), AzurePlugin(), Zoned(), enabled=False).loaded
    for plugin_id in ("aws", "gcp", "azure", "zoned"):
        win.plugins.set_enabled(plugin_id, True)
    monkeypatch.setattr(win, "context_file", lambda context: str(config))
    open_active(win)
    texts = [a.text() for a in win.nav.menu_for(win.nav._clusters["ctx"]).actions() if not a.isSeparator()]
    assert "Cloud settings (Google Cloud)…" in texts and "Zoned: settings for this cluster…" in texts
    assert not [t for t in texts if t.endswith("Plugin: settings for this cluster…") or "(WIP)" in t]
    win.plugins.set_cluster_cloud("ctx", "azure")
    assert win.cluster_settings_entries("ctx")[0] == (win.CLOUD_SETTINGS, "Cloud settings (Azure)…")
    shown = []
    monkeypatch.setattr(CloudSettingsDialog, "exec", lambda self: shown.append((self.context, self.detected)) or 0)
    win.open_cluster_settings(win.CLOUD_SETTINGS, "ctx")
    assert shown == [("ctx", "gcp")]
