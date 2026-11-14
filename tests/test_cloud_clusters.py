"""Clusters from a cloud account: listing and credential commands, output parsing, and merging into a kubeconfig file.
No cloud CLI and no network: the commands are only built, and the merge works on files in tmp_path."""
import json
import os
from pathlib import Path

import pytest
import yaml

from klotse.k8s import cloud_clusters as cloud


def kubeconfig(path, names, current=""):
    data = {"apiVersion": "v1", "kind": "Config", "current-context": current, "preferences": {},
            "clusters": [{"name": n, "cluster": {"server": f"https://{n}.example.test"}} for n in names],
            "users": [{"name": f"user-{n}", "user": {"exec": {"command": "helper"}}} for n in names],
            "contexts": [{"name": n, "context": {"cluster": n, "user": f"user-{n}"}} for n in names]}
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return Path(path)


def test_list_commands_use_each_provider_cli_with_argv_lists():
    assert cloud.list_argv("aws", {"region": "eu-west-1", "profile": "work"}) == [
        "aws", "eks", "list-clusters", "--region", "eu-west-1", "--output", "json", "--profile", "work"]
    assert cloud.list_argv("gcp", {"project": "demo-project", "location": "europe-west1"}) == [
        "gcloud", "container", "clusters", "list", "--project", "demo-project", "--format", "json", "--location", "europe-west1"]
    assert cloud.list_argv("azure", {"resource_group": "rg-demo"}) == ["az", "aks", "list", "--output", "json", "--resource-group", "rg-demo"]
    assert cloud.list_argv("oci", {"compartment": "ocid1.compartment.oc1..demo", "region": "us-phoenix-1"})[:4] == ["oci", "ce", "cluster", "list"]


@pytest.mark.parametrize("provider, scope", [
    ("aws", {"region": ""}), ("aws", {"region": "eu-west-1; rm -rf /"}), ("aws", {"region": "EU"}),
    ("gcp", {"project": ""}), ("gcp", {"project": "demo project"}), ("oci", {"compartment": "ocid1.x", "region": ""}),
    ("oci", {"compartment": "ocid1.x", "region": "us phoenix"}),
])
def test_missing_or_unsafe_scope_values_are_refused_before_any_command(provider, scope):
    with pytest.raises(cloud.CloudError):
        cloud.list_argv(provider, scope)


def test_aws_listing_names_come_from_the_clusters_key():
    text = json.dumps({"clusters": ["demo-prod", "demo-test"]})
    clusters = cloud.parse_clusters("aws", text, {"region": "eu-west-1"})
    assert [c.name for c in clusters] == ["demo-prod", "demo-test"]
    assert clusters[0].location == "eu-west-1"


def test_gcp_azure_and_oci_listings_are_parsed_and_unusable_names_are_left_out():
    gcp = json.dumps([{"name": "demo-gke", "location": "europe-west1", "status": "RUNNING"}, {"name": "bad name"}])
    assert [(c.name, c.location, c.state) for c in cloud.parse_clusters("gcp", gcp)] == [("demo-gke", "europe-west1", "RUNNING")]
    azure = json.dumps([{"name": "demo-aks", "resourceGroup": "rg-demo", "location": "westeurope", "provisioningState": "Succeeded"}])
    assert cloud.parse_clusters("azure", azure)[0].group == "rg-demo"
    oci = json.dumps({"data": [{"name": "demo-oke", "id": "ocid1.cluster.oc1.demo", "lifecycle-state": "ACTIVE"}]})
    found = cloud.parse_clusters("oci", oci, {"region": "us-phoenix-1", "compartment": "ocid1.c"})
    assert found[0].ref == "ocid1.cluster.oc1.demo" and found[0].state == "ACTIVE"


@pytest.mark.parametrize("provider, text", [("aws", "not json"), ("aws", json.dumps({"nope": 1})), ("gcp", json.dumps({"a": 1}))])
def test_unexpected_listing_answers_are_errors(provider, text):
    with pytest.raises(cloud.CloudError):
        cloud.parse_clusters(provider, text, {"region": "eu-west-1"} if provider == "aws" else {})


def test_credential_commands_write_only_to_the_given_temporary_file(tmp_path):
    target = tmp_path / "piece.yaml"
    argv, env = cloud.credentials_argv("aws", cloud.ClusterRef("demo-prod", "demo-prod"), {"region": "eu-west-1"}, target)
    assert argv[:5] == ["aws", "eks", "update-kubeconfig", "--name", "demo-prod"] and str(target) in argv and env == {}
    gcp_ref = cloud.ClusterRef("demo-gke", "demo-gke", "europe-west1")
    argv, env = cloud.credentials_argv("gcp", gcp_ref, {"project": "demo-project"}, target)
    assert env == {"KUBECONFIG": str(target)} and "--location" in argv
    azure_ref = cloud.ClusterRef("demo-aks", "demo-aks", group="rg-demo")
    argv, _ = cloud.credentials_argv("azure", azure_ref, {}, target)
    assert argv[argv.index("--file") + 1] == str(target) and argv[argv.index("--resource-group") + 1] == "rg-demo"
    oci_ref = cloud.ClusterRef("demo-oke", "ocid1.cluster.oc1.demo", "us-phoenix-1")
    argv, _ = cloud.credentials_argv("oci", oci_ref, {"region": "us-phoenix-1", "compartment": "ocid1.c"}, target)
    assert argv[argv.index("--file") + 1] == str(target) and argv[argv.index("--cluster-id") + 1] == "ocid1.cluster.oc1.demo"


def test_default_paths_are_in_dot_kube_and_the_main_file_follows_kubeconfig(tmp_path):
    assert cloud.default_generated_path(tmp_path) == tmp_path / ".kube" / "klotse-cloud.klotse"
    two = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    assert cloud.main_kubeconfig_path({"KUBECONFIG": two}) == tmp_path / "a"
    assert cloud.main_kubeconfig_path({}, tmp_path) == tmp_path / ".kube" / "config"


def test_merge_creates_the_target_and_adds_the_new_entries(tmp_path):
    source = kubeconfig(tmp_path / "piece.yaml", ["demo-prod"])
    target = tmp_path / ".kube" / "klotse-cloud.klotse"
    added, skipped = cloud.merge_into(target, source)
    assert added == ["demo-prod"] and skipped == []
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [c["name"] for c in data["contexts"]] == ["demo-prod"] and data["current-context"] == "demo-prod"


def test_merge_keeps_existing_entries_and_backs_the_file_up(tmp_path):
    target = kubeconfig(tmp_path / "klotse-cloud.klotse", ["demo-old"], current="demo-old")
    source = kubeconfig(tmp_path / "piece.yaml", ["demo-old", "demo-new"])
    added, skipped = cloud.merge_into(target, source)
    assert added == ["demo-new"] and skipped == ["demo-old"]
    assert (tmp_path / "klotse-cloud.klotse.bak").exists()
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [c["name"] for c in data["contexts"]] == ["demo-old", "demo-new"]
    assert data["current-context"] == "demo-old"  # an existing current context is not replaced


def test_merge_without_backup_writes_no_copy(tmp_path):
    target = kubeconfig(tmp_path / "config", ["demo-a"])
    cloud.merge_into(target, kubeconfig(tmp_path / "piece.yaml", ["demo-b"]), backup=False)
    assert not (tmp_path / "config.bak").exists()


def test_a_file_that_is_not_a_kubeconfig_is_refused_and_left_alone(tmp_path):
    target = tmp_path / "config"
    target.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(cloud.CloudError):
        cloud.merge_into(target, kubeconfig(tmp_path / "piece.yaml", ["demo-a"]))
    assert target.read_text(encoding="utf-8") == "- just\n- a list\n"


def test_a_missing_cli_is_a_clear_error_not_a_traceback():
    with pytest.raises(cloud.CloudError, match="not installed or not on the PATH"):
        cloud.run(["klotse-no-such-cli-for-tests", "list"])


def test_a_gke_cluster_without_a_location_gets_it_from_the_scope(monkeypatch):
    cluster = cloud.ClusterRef("demo-gke", "demo-gke")
    filled = cloud.resolve_location("gcp", cluster, {"project": "demo-project", "location": "europe-west1"})
    assert filled.location == "europe-west1"
    monkeypatch.setattr(cloud, "run", lambda argv, env=None, timeout=None: pytest.fail("no lookup needed"))
    assert cloud.resolve_location("gcp", cluster.__class__("demo-gke", "demo-gke", "us-central1"), {"project": "demo-project"}).location == "us-central1"


def test_without_any_location_gcp_is_looked_up_with_a_filtered_list(monkeypatch):
    calls = []

    def run(argv, env=None, timeout=None):
        calls.append(list(argv))
        return "europe-west1-b\n"

    monkeypatch.setattr(cloud, "run", run)
    cluster = cloud.resolve_location("gcp", cloud.ClusterRef("demo-gke", "demo-gke"), {"project": "demo-project"})
    assert cluster.location == "europe-west1-b"
    assert calls == [["gcloud", "container", "clusters", "list", "--project", "demo-project", "--filter", "name=demo-gke", "--format", "value(location)"]]


def test_gcp_credentials_refuse_an_unknown_location_instead_of_running_without_one(tmp_path):
    with pytest.raises(cloud.CloudError, match="location"):
        cloud.credentials_argv("gcp", cloud.ClusterRef("demo-gke", "demo-gke"), {"project": "demo-project"}, tmp_path / "p.yaml")


def test_gcp_listing_reads_the_region_key_too():
    text = json.dumps([{"name": "demo-gke", "region": "europe-west1"}])
    assert cloud.parse_clusters("gcp", text)[0].location == "europe-west1"


def test_an_oci_error_block_gives_its_code_and_message_not_the_closing_brace():
    stderr = ('ServiceError:\n{\n    "client_version": "3.95.1",\n    "code": "NotAuthorizedOrNotFound",\n'
              '    "message": "Authorization failed or requested resource not found: compartment ocid1.c",\n    "status": 404\n}\n')
    assert cloud.failure_reason(stderr, 1) == ("NotAuthorizedOrNotFound: Authorization failed or requested resource not found: "
                                               "compartment ocid1.c")
    assert cloud.failure_reason("plain failure\nlast line\n", 2) == "last line"
    assert cloud.failure_reason("", 3) == "exit status 3"


def test_oci_defaults_come_from_the_config_file_the_cli_reads(tmp_path):
    config = tmp_path / "config"
    config.write_text("[DEFAULT]\nuser=ocid1.user.x\nregion=us-ashburn-1\ntenancy=ocid1.tenancy.oc1..t\n"
                      "[OKE]\nregion=us-phoenix-1\ntenancy=ocid1.tenancy.oc1..other\n", encoding="utf-8")
    assert cloud.oci_defaults({"OCI_CLI_CONFIG_FILE": str(config)}) == {"region": "us-ashburn-1", "compartment": "ocid1.tenancy.oc1..t"}
    assert cloud.oci_defaults({"OCI_CLI_CONFIG_FILE": str(config), "OCI_CLI_PROFILE": "OKE"}) == {
        "region": "us-phoenix-1", "compartment": "ocid1.tenancy.oc1..other", "profile": "OKE"}
    assert cloud.oci_defaults({"OCI_CLI_CONFIG_FILE": str(config), "OCI_CLI_PROFILE": "MISSING"}) == {}
    assert cloud.oci_defaults({"OCI_CLI_CONFIG_FILE": str(tmp_path / "absent")}) == {}
