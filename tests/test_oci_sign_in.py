from klotse.plugins.api import ActionRequest, Message, ResourceInfo, Terminal
from klotse_plugins.oci import OciPlugin, is_oke_node, oci_login_argv


def node(provider, labels=None):
    return ResourceInfo("node-1", "", {"metadata": {"name": "node-1", "labels": labels or {}}, "spec": {"providerID": provider}})


def request(plugin, action, resources, settings=None):
    return ActionRequest(plugin.id, action, "Node", "ctx", resources, settings or {})


def test_oci_login_argv_uses_region_and_profile():
    assert oci_login_argv("us-phoenix-1", "PROD") == ("oci", "session", "authenticate", "--region", "us-phoenix-1", "--profile-name", "PROD")
    assert oci_login_argv("us-phoenix-1", "")[-1] == "DEFAULT"


def test_oke_node_is_recognised_by_its_providerid():
    assert is_oke_node({"spec": {"providerID": "ocid1.instance.oc1.phx.abc"}})
    assert not is_oke_node({"spec": {"providerID": "gce://p/z/n"}})


def test_oci_sign_in_uses_the_node_region_and_the_profile_setting():
    plugin = OciPlugin()
    labels = {"topology.kubernetes.io/region": "us-phoenix-1"}
    result = plugin.run(request(plugin, "sign-in", [node("ocid1.instance.oc1.phx.abc", labels)], {"oci_profile": "OKE"}))
    assert result == Terminal(("oci", "session", "authenticate", "--region", "us-phoenix-1", "--profile-name", "OKE"))


def test_oci_sign_in_needs_a_region_label():
    plugin = OciPlugin()
    result = plugin.run(request(plugin, "sign-in", [node("ocid1.instance.oc1.phx.abc")]))
    assert isinstance(result, Message) and "region" in result.text


def test_oci_sign_in_refuses_a_node_that_is_not_on_oracle_cloud():
    plugin = OciPlugin()
    result = plugin.run(request(plugin, "sign-in", [node("gce://p/z/n")]))
    assert isinstance(result, Message) and result.level == "warning"
