"""EKS clusters for Add clusters from the cloud: list them with the AWS CLI and write one cluster's kubeconfig entry (no Qt).

The plugin owns this logic; core only runs the commands and merges the result into the kubeconfig file."""
import json
import os
import re

from klotse.plugins.api import CloudError, ClusterField, ClusterRef, ClusterSource, SAFE_CLUSTER_VALUE, checked_value, env_value, read_ini_file

REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}")
SOURCE = ClusterSource("eks", "AWS (EKS)", (ClusterField("region", "Region", True), ClusterField("profile", "AWS profile")))


def clean_scope(scope):
    region = checked_value(scope.get("region"), REGION, "Region")
    if not region:
        raise CloudError("Region is required for AWS (EKS)")
    return {"region": region, "profile": checked_value(scope.get("profile"), SAFE_CLUSTER_VALUE, "AWS profile")}


def defaults(env):
    """Profile and region from AWS_PROFILE / AWS_DEFAULT_REGION, else the profile's `region` in the AWS config file."""
    profile = env_value(env, "AWS_PROFILE", "AWS_DEFAULT_PROFILE")
    found = {"profile": profile} if profile else {}
    region = env_value(env, "AWS_DEFAULT_REGION", "AWS_REGION")
    if not region:
        parser = read_ini_file(os.path.expanduser(env.get("AWS_CONFIG_FILE") or "~/.aws/config"))
        section = f"profile {profile}" if profile and profile != "default" else "default"
        if parser is not None and parser.has_section(section):
            region = parser[section].get("region", "").strip()
    if region:
        found["region"] = region
    return found


def parse(text, region):
    try:
        data = json.loads(text or "null")
    except ValueError:
        raise CloudError("the AWS CLI answered with something that is not JSON") from None
    names = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(names, list):
        raise CloudError("the AWS CLI answered with an unexpected list")
    clusters = []
    seen = set()
    for item in names:
        name = item if isinstance(item, str) else ""
        if SAFE_CLUSTER_VALUE.fullmatch(name) and name not in seen:
            seen.add(name)
            clusters.append(ClusterRef(name, name, region))
    return clusters


def list_clusters(scope, run):
    s = clean_scope(scope)
    argv = ["aws", "eks", "list-clusters", "--region", s["region"], "--output", "json"] + (["--profile", s["profile"]] if s["profile"] else [])
    return parse(run(argv), s["region"])


def cluster_credentials(cluster, scope, target, run):
    s = clean_scope(scope)
    argv = ["aws", "eks", "update-kubeconfig", "--name", cluster.name, "--region", s["region"], "--kubeconfig", str(target)]
    run(argv + (["--profile", s["profile"]] if s["profile"] else []))
