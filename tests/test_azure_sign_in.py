"""Azure (AKS) sign-in action, and the manifests of the cloud sign-in plugins matching their classes (no network)."""
import importlib
import json
from pathlib import Path

import pytest

from klotse.plugins.api import ActionRequest, Message, ResourceInfo, Terminal
from klotse_plugins.azure import AzurePlugin, az_login_argv, is_aks_node

ROOT = Path(__file__).resolve().parents[1] / "src" / "klotse_plugins"


def node(provider):
    return ResourceInfo("node-1", "", {"metadata": {"name": "node-1"}, "spec": {"providerID": provider}})


def request(action, resources, settings=None):
    return ActionRequest("azure", action, "Node", "ctx", resources, settings or {})


AKS = "azure:///subscriptions/1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss/virtualMachines/0"


def test_az_login_argv():
    assert az_login_argv() == ("az", "login")
    assert az_login_argv("contoso.onmicrosoft.com", True) == ("az", "login", "--tenant", "contoso.onmicrosoft.com", "--use-device-code")


def test_aks_node_is_recognised_by_its_providerid():
    assert is_aks_node({"spec": {"providerID": AKS}})
    assert not is_aks_node({"spec": {"providerID": "gce://p/z/n"}})
    assert not is_aks_node({})


def test_sign_in_opens_az_login_in_a_terminal():
    plugin = AzurePlugin()
    assert plugin.run(request("sign-in", [node(AKS)])) == Terminal(("az", "login"))


def test_sign_in_passes_tenant_and_device_code_settings():
    plugin = AzurePlugin()
    settings = {"azure_tenant": "contoso.onmicrosoft.com", "azure_device_code": "true"}
    assert plugin.run(request("sign-in", [node(AKS)], settings)) == Terminal(
        ("az", "login", "--tenant", "contoso.onmicrosoft.com", "--use-device-code"))


@pytest.mark.parametrize("tenant", ["a b", "x;rm -rf", "$(id)"])
def test_a_tenant_with_shell_characters_is_refused(tenant):
    plugin = AzurePlugin()
    result = plugin.run(request("sign-in", [node(AKS)], {"azure_tenant": tenant}))
    assert isinstance(result, Message) and result.level == "warning"


def test_sign_in_refuses_a_node_that_is_not_on_azure():
    plugin = AzurePlugin()
    result = plugin.run(request("sign-in", [node("aws:///eu-west-1a/i-0abc")]))
    assert isinstance(result, Message) and result.level == "warning"


@pytest.mark.parametrize("module, cls", [("azure", "AzurePlugin")])
def test_manifest_matches_the_plugin_class(module, cls):
    manifest = json.loads((ROOT / f"{module}.json").read_text(encoding="utf-8"))
    plugin = getattr(importlib.import_module(f"klotse_plugins.{module}"), cls)()
    assert manifest["id"] == plugin.id and manifest["name"] == plugin.name and manifest["requires_api"] == plugin.requires_api
    assert [a["key"] for a in manifest["actions"]] == [a.key for a in plugin.actions]
    assert [s["key"] for s in manifest["settings"]] == [s.key for s in plugin.settings]
