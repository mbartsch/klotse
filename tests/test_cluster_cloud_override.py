"""The per-cluster `cloud` setting: a cluster whose nodes do not report their cloud is accepted when the setting names the plugin's
cloud; `auto` keeps the providerID check. No cloud CLI and no network: the sign-in only builds its command."""
import pytest

from klotse.plugins.api import ActionRequest, Message, ResourceInfo, Terminal
from klotse_plugins.azure import AzurePlugin
from klotse_plugins.gcp import GcpPlugin
from klotse_plugins.oci import OciPlugin

AWS_NODE = {"metadata": {"name": "n1", "labels": {"topology.kubernetes.io/region": "eu-west-1"}}, "spec": {"providerID": "aws:///eu-west-1a/i-0abc"}}


def request(plugin, settings):
    return ActionRequest(plugin.id, "sign-in", "Node", "ctx", [ResourceInfo("n1", "", AWS_NODE)], settings)


@pytest.mark.parametrize("plugin, value", [(GcpPlugin(), "gcp"), (AzurePlugin(), "azure"), (OciPlugin(), "oci")])
def test_auto_keeps_the_providerid_check(plugin, value):
    result = plugin.run(request(plugin, {"cloud": "auto"}))
    assert isinstance(result, Message) and result.level == "warning"
    assert isinstance(plugin.run(request(plugin, {})), Message)  # an unset setting is auto too


def test_gcp_setting_accepts_a_node_that_is_not_on_gke():
    assert GcpPlugin().run(request(GcpPlugin(), {"cloud": "gcp"})) == Terminal(("gcloud", "auth", "login"))


def test_azure_setting_accepts_a_node_that_is_not_on_aks():
    assert AzurePlugin().run(request(AzurePlugin(), {"cloud": "azure"})) == Terminal(("az", "login"))


def test_oci_setting_accepts_a_node_that_is_not_on_oke_and_uses_its_region_label():
    result = OciPlugin().run(request(OciPlugin(), {"cloud": "oci", "oci_profile": "OKE"}))
    assert result == Terminal(("oci", "session", "authenticate", "--region", "eu-west-1", "--profile-name", "OKE"))


def test_a_setting_for_another_cloud_does_not_accept_the_node():
    result = GcpPlugin().run(request(GcpPlugin(), {"cloud": "azure"}))
    assert isinstance(result, Message)
