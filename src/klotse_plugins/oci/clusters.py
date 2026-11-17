"""OKE clusters for Add clusters from the cloud: list them with the OCI CLI and write one cluster's kubeconfig entry (no Qt).

The plugin owns this logic; core only runs the commands and merges the result into the kubeconfig file."""
import json
import os
import re

from klotse.plugins.api import CloudError, ClusterField, ClusterRef, ClusterSource, SAFE_CLUSTER_VALUE, checked_value, env_value, read_ini_file

REGION = re.compile(r"[a-z]{2,4}(?:-[a-z]+)+-\d")
SOURCE = ClusterSource("oke", "Oracle Cloud (OKE)", (ClusterField("compartment", "Compartment OCID", True), ClusterField("region", "Region", True),
                                                      ClusterField("profile", "OCI config profile")))


def clean_scope(scope):
    s = {"compartment": checked_value(scope.get("compartment"), SAFE_CLUSTER_VALUE, "Compartment OCID"),
         "region": checked_value(scope.get("region"), REGION, "Region"),
         "profile": checked_value(scope.get("profile"), SAFE_CLUSTER_VALUE, "OCI config profile")}
    for key, label in (("compartment", "Compartment OCID"), ("region", "Region")):
        if not s[key]:
            raise CloudError(f"{label} is required for Oracle Cloud (OKE)")
    return s


def defaults(env):
    """Region, compartment and profile from the OCI config file the CLI reads (OCI_CLI_CONFIG_FILE, default ~/.oci/config;
    OCI_CLI_PROFILE, default DEFAULT). The region is the profile's `region`; the compartment defaults to its `tenancy`.
    Nothing found, or an unreadable file, gives {}. The endpoint is not read here: the OCI CLI itself honours OCI_CLI_ENDPOINT."""
    path = os.path.expanduser(env.get("OCI_CLI_CONFIG_FILE") or "~/.oci/config")
    profile = (env.get("OCI_CLI_PROFILE") or "DEFAULT").strip()
    parser = read_ini_file(path)
    if parser is None:
        return {}
    if profile == "DEFAULT":
        values = dict(parser.defaults())
    elif parser.has_section(profile):
        values = dict(parser[profile])
    else:
        return {}
    found = {}
    if values.get("region", "").strip():
        found["region"] = values["region"].strip()
    if values.get("tenancy", "").strip():
        found["compartment"] = values["tenancy"].strip()
    if env.get("OCI_CLI_PROFILE", "").strip():
        found["profile"] = profile
    return found


def parse(text, region):
    try:
        data = json.loads(text or "null")
    except ValueError:
        raise CloudError("the OCI CLI answered with something that is not JSON") from None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CloudError("the OCI CLI answered with an unexpected list")
    clusters = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        ocid = str(item.get("id") or "")
        if SAFE_CLUSTER_VALUE.fullmatch(name) and ocid.startswith("ocid1.") and ocid not in seen:
            seen.add(ocid)
            clusters.append(ClusterRef(name, ocid, region, state=str(item.get("lifecycle-state") or "")))
    return clusters


def list_clusters(scope, run):
    s = clean_scope(scope)
    argv = ["oci", "ce", "cluster", "list", "--compartment-id", s["compartment"], "--region", s["region"], "--all"] + (
        ["--profile", s["profile"]] if s["profile"] else [])
    return parse(run(argv), s["region"])


def cluster_credentials(cluster, scope, target, run):
    s = clean_scope(scope)
    argv = ["oci", "ce", "cluster", "create-kubeconfig", "--cluster-id", cluster.ref, "--file", str(target), "--region", s["region"],
            "--token-version", "2.0.0"]
    run(argv + (["--profile", s["profile"]] if s["profile"] else []))
