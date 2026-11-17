"""GKE clusters for Add clusters from the cloud: list them with gcloud and write one cluster's kubeconfig entry (no Qt).

The plugin owns this logic; core only runs the commands and merges the result into the kubeconfig file."""
import json
import os
import re

from klotse.plugins.api import CloudError, ClusterField, ClusterRef, ClusterSource, SAFE_CLUSTER_VALUE, checked_value, env_value, read_ini_file

SOURCE = ClusterSource("gke", "Google Cloud (GKE)", (ClusterField("project", "Project ID", True),
                                                       ClusterField("location", "Location (zone or region; optional, looked up when empty)")))


def clean_scope(scope):
    project = checked_value(scope.get("project"), SAFE_CLUSTER_VALUE, "Project ID")
    if not project:
        raise CloudError("Project ID is required for Google Cloud (GKE)")
    return {"project": project, "location": checked_value(scope.get("location"), SAFE_CLUSTER_VALUE, "Location")}


def defaults(env):
    """The project of the active gcloud configuration. Location is left empty: an empty location lists every cluster."""
    config_dir = os.path.expanduser(env.get("CLOUDSDK_CONFIG") or "~/.config/gcloud")
    name = env_value(env, "CLOUDSDK_ACTIVE_CONFIG_NAME")
    if not name:
        try:
            with open(os.path.join(config_dir, "active_config"), encoding="utf-8") as handle:
                name = handle.read().strip()
        except OSError:
            name = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        name = "default"
    project = env_value(env, "CLOUDSDK_CORE_PROJECT")
    if not project:
        parser = read_ini_file(os.path.join(config_dir, "configurations", f"config_{name}"))
        if parser is not None and parser.has_section("core"):
            project = parser["core"].get("project", "").strip()
    return {"project": project} if project else {}


def parse(text):
    try:
        data = json.loads(text or "null")
    except ValueError:
        raise CloudError("gcloud answered with something that is not JSON") from None
    if not isinstance(data, list):
        raise CloudError("gcloud answered with an unexpected list")
    clusters = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not SAFE_CLUSTER_VALUE.fullmatch(name):
            continue
        location = str(item.get("location") or item.get("zone") or item.get("region") or "")
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        clusters.append(ClusterRef(name, name, location, state=str(item.get("status") or "")))
    return clusters


def list_clusters(scope, run):
    s = clean_scope(scope)
    argv = ["gcloud", "container", "clusters", "list", "--project", s["project"], "--format", "json"] + (
        ["--location", s["location"]] if s["location"] else [])
    return parse(run(argv))


def cluster_credentials(cluster, scope, target, run):
    """get-credentials needs --location (a zone or a region). When the list gave none, the Location field is used, else a
    filtered list must give one distinct location; otherwise no credentials command is run and the user is told."""
    s = clean_scope(scope)
    location = cluster.location or s["location"]
    if not location:
        found = run(["gcloud", "container", "clusters", "list", "--project", s["project"], "--filter", f"name={cluster.name}",
                     "--format", "value(location)"])
        locations = {line.strip() for line in found.splitlines() if line.strip()}
        if len(locations) > 1:
            raise CloudError(f"multiple locations found for {cluster.name}: enter its zone or region in Location and try again")
        location = next(iter(locations), "")
    if not location or not SAFE_CLUSTER_VALUE.fullmatch(location):
        raise CloudError(f"could not find the location of {cluster.name}: enter its zone or region in Location and try again")
    run(["gcloud", "container", "clusters", "get-credentials", cluster.name, "--project", s["project"], "--location", location],
        env={"KUBECONFIG": str(target)})
