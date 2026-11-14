"""Add clusters from the cloud, per cloud plugin: the listing, the credentials command, the local defaults, and which plugins the
dialog offers. The CLI is replaced by a fake that records the argv (no cloud, no network)."""
import json

import pytest

from klotse.plugins.api import CloudError, ClusterRef
from klotse.plugins.manager import PluginManager
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.aws import clusters as eks
from klotse_plugins.azure import AzurePlugin
from klotse_plugins.azure import clusters as aks
from klotse_plugins.gcp import GcpPlugin
from klotse_plugins.gcp import clusters as gke
from klotse_plugins.oci import OciPlugin
from klotse_plugins.oci import clusters as oke


class FakeRun:
    """Records every argv and answers with the next canned output (or the same one)."""

    def __init__(self, *answers):
        self.calls, self.env_calls = [], []
        self.answers = list(answers)

    def __call__(self, argv, env=None, timeout=None):
        self.calls.append(list(argv))
        self.env_calls.append(env)
        return self.answers.pop(0) if len(self.answers) > 1 else (self.answers[0] if self.answers else "")


# ---- AWS (EKS) ---------------------------------------------------------------------------------------------------------------
def test_eks_listing_runs_the_aws_cli_and_reads_the_names():
    run = FakeRun(json.dumps({"clusters": ["demo-prod", "demo-test"]}))
    found = AwsPlugin().list_clusters(eks.SOURCE, {"region": "eu-west-1", "profile": "work"}, run)
    assert run.calls == [["aws", "eks", "list-clusters", "--region", "eu-west-1", "--output", "json", "--profile", "work"]]
    assert [(c.name, c.location) for c in found] == [("demo-prod", "eu-west-1"), ("demo-test", "eu-west-1")]


@pytest.mark.parametrize("scope", [{}, {"region": "eu-west-1; rm -rf /"}, {"region": "EU"}])
def test_eks_refuses_a_missing_or_unsafe_region_before_running_anything(scope):
    run = FakeRun("{}")
    with pytest.raises(CloudError):
        AwsPlugin().list_clusters(eks.SOURCE, scope, run)
    assert run.calls == []


def test_eks_credentials_write_into_the_given_file_with_the_profile():
    run = FakeRun("")
    AwsPlugin().cluster_credentials(eks.SOURCE, ClusterRef("demo-prod", "demo-prod"), {"region": "eu-west-1", "profile": "work"},
                                    "piece.yaml", run)
    assert run.calls == [["aws", "eks", "update-kubeconfig", "--name", "demo-prod", "--region", "eu-west-1", "--kubeconfig",
                          "piece.yaml", "--profile", "work"]]


def test_eks_defaults_come_from_the_environment_and_the_config_file(tmp_path):
    config = tmp_path / "config"
    config.write_text("[profile work]\nregion = us-east-2\n", encoding="utf-8")
    found = AwsPlugin().cluster_defaults(eks.SOURCE, {"AWS_PROFILE": "work", "AWS_CONFIG_FILE": str(config)})
    assert found == {"profile": "work", "region": "us-east-2"}


# ---- Google Cloud (GKE) ------------------------------------------------------------------------------------------------------
def test_gke_listing_reads_the_location_from_any_of_its_keys():
    text = json.dumps([{"name": "demo-gke", "location": "europe-west1", "status": "RUNNING"},
                       {"name": "demo-zonal", "zone": "europe-west1-b"}, {"name": "bad name"}])
    run = FakeRun(text)
    found = GcpPlugin().list_clusters(gke.SOURCE, {"project": "demo-project"}, run)
    assert run.calls == [["gcloud", "container", "clusters", "list", "--project", "demo-project", "--format", "json"]]
    assert [(c.name, c.location) for c in found] == [("demo-gke", "europe-west1"), ("demo-zonal", "europe-west1-b")]


def test_gke_credentials_use_the_location_of_the_cluster_and_its_own_kubeconfig():
    run = FakeRun("")
    GcpPlugin().cluster_credentials(gke.SOURCE, ClusterRef("demo-gke", "demo-gke", "europe-west1"), {"project": "demo-project"},
                                    "piece.yaml", run)
    assert run.calls == [["gcloud", "container", "clusters", "get-credentials", "demo-gke", "--project", "demo-project", "--location",
                          "europe-west1"]]
    assert run.env_calls == [{"KUBECONFIG": "piece.yaml"}]


def test_gke_without_any_location_is_looked_up_with_a_filtered_list():
    run = FakeRun("europe-west1-b\n", "")
    GcpPlugin().cluster_credentials(gke.SOURCE, ClusterRef("demo-gke", "demo-gke"), {"project": "demo-project"}, "piece.yaml", run)
    assert run.calls[0] == ["gcloud", "container", "clusters", "list", "--project", "demo-project", "--filter", "name=demo-gke",
                            "--format", "value(location)"]
    assert run.calls[1][-2:] == ["--location", "europe-west1-b"]


def test_gke_refuses_to_run_get_credentials_when_no_location_is_known():
    run = FakeRun("")
    with pytest.raises(CloudError, match="location of demo-gke"):
        GcpPlugin().cluster_credentials(gke.SOURCE, ClusterRef("demo-gke", "demo-gke"), {"project": "demo-project"}, "piece.yaml", run)
    assert all("get-credentials" not in call for call in run.calls)


def test_gke_default_project_comes_from_the_active_gcloud_configuration(tmp_path):
    (tmp_path / "configurations").mkdir()
    (tmp_path / "active_config").write_text("work\n", encoding="utf-8")
    (tmp_path / "configurations" / "config_work").write_text("[core]\nproject = demo-project\n", encoding="utf-8")
    assert GcpPlugin().cluster_defaults(gke.SOURCE, {"CLOUDSDK_CONFIG": str(tmp_path)}) == {"project": "demo-project"}


# ---- Oracle Cloud (OKE) ------------------------------------------------------------------------------------------------------
def test_oke_listing_keeps_only_clusters_with_an_ocid_and_names_them_by_it():
    text = json.dumps({"data": [{"name": "demo-oke", "id": "ocid1.cluster.oc1.demo", "lifecycle-state": "ACTIVE"},
                                {"name": "no-id", "id": "not-an-ocid"}]})
    run = FakeRun(text)
    found = OciPlugin().list_clusters(oke.SOURCE, {"compartment": "ocid1.c", "region": "us-phoenix-1"}, run)
    assert run.calls == [["oci", "ce", "cluster", "list", "--compartment-id", "ocid1.c", "--region", "us-phoenix-1", "--all"]]
    assert [(c.name, c.ref, c.state) for c in found] == [("demo-oke", "ocid1.cluster.oc1.demo", "ACTIVE")]


def test_oke_credentials_use_the_cluster_ocid_and_token_version_2():
    run = FakeRun("")
    OciPlugin().cluster_credentials(oke.SOURCE, ClusterRef("demo-oke", "ocid1.cluster.oc1.demo", "us-phoenix-1"),
                                    {"compartment": "ocid1.c", "region": "us-phoenix-1", "profile": "PROD"}, "piece.yaml", run)
    assert run.calls == [["oci", "ce", "cluster", "create-kubeconfig", "--cluster-id", "ocid1.cluster.oc1.demo", "--file",
                          "piece.yaml", "--region", "us-phoenix-1", "--token-version", "2.0.0", "--profile", "PROD"]]


def test_oke_defaults_come_from_the_oci_config_file(tmp_path):
    config = tmp_path / "config"
    config.write_text("[DEFAULT]\nregion = us-ashburn-1\ntenancy = ocid1.tenancy.oc1..demo\n", encoding="utf-8")
    assert OciPlugin().cluster_defaults(oke.SOURCE, {"OCI_CLI_CONFIG_FILE": str(config)}) == {
        "region": "us-ashburn-1", "compartment": "ocid1.tenancy.oc1..demo"}


def test_oke_refuses_an_unsafe_region():
    with pytest.raises(CloudError):
        OciPlugin().list_clusters(oke.SOURCE, {"compartment": "ocid1.c", "region": "us phoenix"}, FakeRun("{}"))


# ---- Azure (AKS) -------------------------------------------------------------------------------------------------------------
def test_aks_listing_and_credentials():
    text = json.dumps([{"name": "demo-aks", "resourceGroup": "rg-demo", "location": "westeurope", "provisioningState": "Succeeded"}])
    run = FakeRun(text, "")
    found = AzurePlugin().list_clusters(aks.SOURCE, {"resource_group": "rg-demo"}, run)
    assert found[0].group == "rg-demo" and found[0].state == "Succeeded"
    AzurePlugin().cluster_credentials(aks.SOURCE, found[0], {"subscription": "demo-sub"}, "piece.yaml", run)
    assert run.calls[1] == ["az", "aks", "get-credentials", "--name", "demo-aks", "--file", "piece.yaml", "--resource-group", "rg-demo",
                            "--subscription", "demo-sub"]


def test_aks_default_subscription_is_the_one_marked_default_and_a_byte_order_mark_is_accepted(tmp_path):
    (tmp_path / "azureProfile.json").write_text(
        "﻿" + json.dumps({"subscriptions": [{"id": "sub-1", "isDefault": False}, {"id": "sub-2", "isDefault": True}]}),
        encoding="utf-8")
    assert AzurePlugin().cluster_defaults(aks.SOURCE, {"AZURE_CONFIG_DIR": str(tmp_path)}) == {"subscription": "sub-2"}


# ---- plugins that do not list, and which plugins the dialog offers -----------------------------------------------------------
def test_a_plugin_without_a_listing_says_so():
    with pytest.raises(CloudError, match="does not list clusters"):
        AwsPlugin().list_clusters(gke.SOURCE, {}, FakeRun("{}"))


def test_only_enabled_plugins_offer_cluster_sources():
    class Manager:
        def active(self):
            return [AwsPlugin()]  # only AWS is enabled

    sources = PluginManager.cluster_sources(Manager())
    assert [(plugin.id, source.key) for plugin, source in sources] == [("aws", "eks")]


def test_no_enabled_plugin_gives_no_sources():
    class Manager:
        def active(self):
            return []

    assert PluginManager.cluster_sources(Manager()) == []
