import importlib

from klotse.plugins.api import ActionRequest, Message, ResourceInfo, Terminal
from klotse_plugins.gcp import GcpPlugin, gcloud_login_argv, is_gke_node


def node(provider, labels=None):
    return ResourceInfo("node-1", "", {"metadata": {"name": "node-1", "labels": labels or {}}, "spec": {"providerID": provider}})


def request(plugin, action, resources, settings=None):
    return ActionRequest(plugin.id, action, "Node", "ctx", resources, settings or {})


def test_gcloud_login_argv():
    assert gcloud_login_argv() == ("gcloud", "auth", "login")


def test_gke_node_is_recognised_by_its_providerid():
    assert is_gke_node({"spec": {"providerID": "gce://my-project/europe-west1-b/gke-node-1"}})
    assert not is_gke_node({"spec": {"providerID": "aws:///eu-west-1a/i-0abc"}})
    assert not is_gke_node({})


def test_gcp_sign_in_opens_gcloud_in_a_terminal():
    plugin = GcpPlugin()
    result = plugin.run(request(plugin, "sign-in", [node("gce://p/z/n")]))
    assert result == Terminal(("gcloud", "auth", "login"))


def test_gcp_sign_in_refuses_a_node_that_is_not_on_google_cloud():
    plugin = GcpPlugin()
    result = plugin.run(request(plugin, "sign-in", [node("aws:///eu-west-1a/i-0abc")]))
    assert isinstance(result, Message) and result.level == "warning" and "gce://" in result.text


def test_bundled_gcp_module_is_importable_and_named():
    assert importlib.import_module("klotse_plugins.gcp").plugin.id == "gcp"
