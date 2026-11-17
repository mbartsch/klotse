"""Synthetic, offline coverage for the bounded GCP metadata plugin."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from klotse.plugins import ActionRequest, EnrichRequest, Message, OpenUrl, ResourceInfo
from klotse.plugins.manifest import read_manifest
from klotse_plugins.gcp import GcpPlugin
from klotse_plugins.gcp.nodeinfo import console_url, instance_ref, node_values


PROVIDER = "gce://demo-project/europe-west1-b/gke-node-1"


def test_bundled_manifest_matches_runtime_declarations():
    manifest = read_manifest(Path(__file__).resolve().parents[1] / "src/klotse_plugins/gcp.json")
    plugin = GcpPlugin()
    for field in ("id", "name", "version", "requires_api", "description", "columns", "actions",
                  "overview_cards", "settings", "cluster_settings"):
        assert getattr(manifest, field) == getattr(plugin, field), field


def node(provider=PROVIDER, labels=None, image=None, name="node-1"):
    return ResourceInfo(name, "", {"spec": {"providerID": provider},
                                 "metadata": {"labels": labels or {}},
                                 "status": {"nodeInfo": {"osImage": image}}})


def enrich(resources, settings=None, kind="Node"):
    return GcpPlugin().enrich(EnrichRequest("gcp", kind, "ctx", resources, settings or {}))


def console(resources, settings=None, kind="Node"):
    return GcpPlugin().run(ActionRequest("gcp", "console", kind, "ctx", resources, settings or {}))


def test_metadata_columns_are_plain_values_without_cloud_access():
    resource = node(labels={"node.kubernetes.io/instance-type": "e2-standard-4",
                            "cloud.google.com/gke-spot": "true"}, image="Container-Optimized OS")
    before = deepcopy(resource.raw)
    assert enrich([resource]) == {"node-1": {"machine_type": "e2-standard-4", "capacity": "Spot",
                                           "image": "Container-Optimized OS"}}
    assert resource.raw == before
    plugin = GcpPlugin()
    assert {column.key for column in plugin.columns} == {"machine_type", "capacity", "image"}
    assert all(column.kinds == ("Node",) for column in plugin.columns)
    assert not plugin.overview_cards
    assert "WIP" in plugin.name and "help appreciated" in plugin.description


@pytest.mark.parametrize("labels,expected", [
    ({}, ""),
    ({"cloud.google.com/gke-spot": "false"}, ""),
    ({"cloud.google.com/gke-spot": "TRUE"}, ""),
    ({"cloud.google.com/gke-spot": True}, ""),
    ({"cloud.google.com/gke-spot": "true"}, "Spot"),
    ({"cloud.google.com/gke-preemptible": "true"}, "Preemptible"),
    ({"cloud.google.com/gke-provisioning": "spot"}, "Spot"),
    ({"cloud.google.com/gke-provisioning": "preemptible"}, "Preemptible"),
    ({"cloud.google.com/gke-provisioning": "standard"}, "Standard"),
    ({"cloud.google.com/gke-provisioning": "unknown"}, ""),
    ({"cloud.google.com/gke-spot": "false", "cloud.google.com/gke-preemptible": "false"}, "Standard"),
])
def test_capacity_requires_explicit_metadata(labels, expected):
    assert node_values(node(labels=labels).raw)["capacity"] == expected


def test_missing_labels_and_os_image_fallbacks():
    assert enrich([node()]) == {"node-1": {"machine_type": "", "capacity": "", "image": ""}}
    resource = node(labels={"beta.kubernetes.io/instance-type": "n1-standard-1",
                            "cloud.google.com/gke-os-distribution": "cos"})
    assert node_values(resource.raw) == {"machine_type": "n1-standard-1", "capacity": "", "image": "cos"}
    resource.raw["metadata"]["labels"]["node.kubernetes.io/instance-type"] = "e2-medium"
    assert node_values(resource.raw)["machine_type"] == "e2-medium"


@pytest.mark.parametrize("raw", [None, [], {}, {"metadata": None, "status": None},
                                   {"metadata": {"labels": []}, "status": {"nodeInfo": "bad"}}])
def test_partial_metadata_is_safe(raw):
    assert node_values(raw) == {"machine_type": "", "capacity": "", "image": ""}


def test_text_is_not_interpreted_as_html_or_inserted_into_console_url():
    value = '<script>alert("x")</script>&project=other#fragment'
    resource = node(labels={"node.kubernetes.io/instance-type": value}, image=value)
    assert enrich([resource])[resource.key] == {"machine_type": value, "capacity": "", "image": value}
    assert value not in console([resource]).url


@pytest.mark.parametrize("provider", [
    None, 1, {}, [], "", "aws:///eu-west-1a/i-0abc", "gce://", "gce://p/z/n",
    "GCE://demo-project/europe-west1-b/node", PROVIDER + "/extra", PROVIDER + "?project=evil",
    PROVIDER + "#fragment", PROVIDER + "\n", PROVIDER + " ",
    "gce://evil.example@demo-project/europe-west1-b/node",
    "gce://demo-project/../node", "gce://demo-project/europe-west1-b/..",
    "gce://demo-project/europe-west1-b/node%2F..%2Fother",
    "gce://demo-project/europe-west1-b/node&project=evil",
    "gce://demo-project/europe-west1-b/node\\evil",
    "gce://demo-project/europe-west1-b/<script>",
    "gce://demo-project/europe-west1-b/nodé",
    "gce://demo-project/europe-west1-b/" + "n" * 64,
])
def test_untrusted_provider_cannot_build_a_console_link(provider):
    resource = node(provider)
    assert instance_ref(resource.raw) is None
    assert console_url(resource.raw) is None
    assert isinstance(console([resource], {"cloud": "gcp"}), Message)
    action = next(action for action in GcpPlugin().actions if action.key == "console")
    assert not GcpPlugin().applies(action, resource)


def test_console_result_has_fixed_https_host_and_separate_project_query():
    result = console([node()])
    assert result == OpenUrl("https://console.cloud.google.com/compute/instancesDetail/zones/"
                             "europe-west1-b/instances/gke-node-1?project=demo-project")
    url = urlsplit(result.url)
    assert (url.scheme, url.netloc, url.fragment) == ("https", "console.cloud.google.com", "")
    assert parse_qs(url.query) == {"project": ["demo-project"]}
    assert instance_ref(node().raw) == ("demo-project", "europe-west1-b", "gke-node-1")
    action = next(action for action in GcpPlugin().actions if action.key == "console")
    assert GcpPlugin().applies(action, node())


@pytest.mark.parametrize("raw", [None, [], {}, {"spec": None}, {"spec": []}, {"spec": {"providerID": False}}])
def test_malformed_manifest_has_no_instance_ref(raw):
    assert instance_ref(raw) is None
    assert console_url(raw) is None


def test_cloud_override_allows_metadata_but_never_unvalidated_console_links():
    resource = node("aws:///zone/instance", labels={"node.kubernetes.io/instance-type": "custom"})
    assert enrich([resource]) == {}
    assert enrich([resource], {"cloud": "gcp"})[resource.key]["machine_type"] == "custom"
    assert isinstance(console([resource], {"cloud": "gcp"}), Message)
    assert enrich([node()], kind="Pod") == {}


@pytest.mark.parametrize("resources,kind", [([], "Node"), ([node(), node(name="node-2")], "Node"), ([node()], "Pod")])
def test_console_refuses_wrong_kind_and_selection(resources, kind):
    assert isinstance(console(resources, kind=kind), Message)
