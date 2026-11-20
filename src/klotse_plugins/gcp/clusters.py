"""GKE clusters for Add clusters from the cloud: list them with gcloud and write one cluster's kubeconfig entry (no Qt).

The plugin owns this logic; core only runs the commands and merges the result into the kubeconfig file."""
import json
import os
import re
import urllib.parse
from pathlib import Path

import yaml

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
    if local_emulator():
        use_emulator_token(target)


ENDPOINT_VARIABLE = "CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER"  # gcloud's GKE API address; Floci sets it to a local one
EMULATOR_TOKEN = "klotse-local-emulator"  # not a secret: only written for clusters on this computer, whose emulator accepts any token
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def _loopback(url):
    try:
        host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS or host.endswith(".localhost")


def local_emulator(environ=None):
    """Whether gcloud is pointed at a GKE emulator on this computer (Floci): CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER names
    localhost, a *.localhost name, 127.0.0.1 or ::1. Real Google Cloud never sets it, so imports from it are unchanged."""
    return _loopback((os.environ if environ is None else environ).get(ENDPOINT_VARIABLE, "").strip())


def use_emulator_token(target):
    """Rewrite the kubeconfig gcloud wrote for an emulator cluster: its user gets a fixed test token instead of
    gke-gcloud-auth-plugin, which needs a Google sign-in the emulator does not have (#795). Only clusters whose server is on this
    computer are changed, so the token never goes elsewhere. The cluster's CA from the emulator is kept and TLS stays verified;
    a cluster without a CA is refused, never turned into an unverified one."""
    path = Path(target)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise CloudError("gcloud wrote a kubeconfig that cannot be read") from None
    if not isinstance(data, dict):
        raise CloudError("gcloud wrote something that is not a kubeconfig")
    clusters = {item.get("name"): item.get("cluster") for item in data.get("clusters") or [] if isinstance(item, dict)}
    users = {item.get("name"): item for item in data.get("users") or [] if isinstance(item, dict)}
    changed = False
    for item in data.get("contexts") or []:
        context = item.get("context") if isinstance(item, dict) else None
        if not isinstance(context, dict):
            continue
        cluster, user = clusters.get(context.get("cluster")), users.get(context.get("user"))
        if not isinstance(cluster, dict) or user is None or not _loopback(cluster.get("server")):
            continue
        if not cluster.get("certificate-authority-data") and not cluster.get("certificate-authority"):
            # Floci fills masterAuth.clusterCaCertificate from the k3s kubeconfig only once the API server is ready, and never
            # in mock mode (FLOCI_GCP_SERVICES_GKE_MOCK, which starts no API server at all).
            raise CloudError(f"the local emulator gave no CA certificate for {item.get('name')}; Klotse does not turn off TLS "
                             "verification, so the cluster was not added. The emulator reports the CA only after the cluster's "
                             "Kubernetes API has finished starting (and never in mock mode, which has no API server): wait, "
                             "then add the cluster again")
        user["user"] = {"token": EMULATOR_TOKEN}
        changed = True
    if changed:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
