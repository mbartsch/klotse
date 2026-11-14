"""AKS clusters for Add clusters from the cloud: list them with the Azure CLI and write one cluster's kubeconfig entry (no Qt).

The plugin owns this logic; core only runs the commands and merges the result into the kubeconfig file."""
import json
import os

from klotse.plugins.api import CloudError, ClusterField, ClusterRef, ClusterSource, SAFE_CLUSTER_VALUE, checked_value

SOURCE = ClusterSource("aks", "Azure (AKS)", (ClusterField("subscription", "Subscription (id or name)"), ClusterField("resource_group", "Resource group")))


def clean_scope(scope):
    return {"subscription": checked_value(scope.get("subscription"), SAFE_CLUSTER_VALUE, "Subscription"),
            "resource_group": checked_value(scope.get("resource_group"), SAFE_CLUSTER_VALUE, "Resource group")}


def defaults(env):
    """The default subscription of the Azure CLI (the one `az account set` chose). The resource group is left empty:
    an empty group lists every cluster in the subscription."""
    config_dir = os.path.expanduser(env.get("AZURE_CONFIG_DIR") or "~/.azure")
    try:
        with open(os.path.join(config_dir, "azureProfile.json"), encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    for item in (data.get("subscriptions") or []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("isDefault") and item.get("id"):
            return {"subscription": str(item["id"])}
    return {}


def parse(text):
    try:
        data = json.loads(text or "null")
    except ValueError:
        raise CloudError("the Azure CLI answered with something that is not JSON") from None
    if not isinstance(data, list):
        raise CloudError("the Azure CLI answered with an unexpected list")
    clusters = []
    for item in data:
        name = str((item or {}).get("name") or "") if isinstance(item, dict) else ""
        if not SAFE_CLUSTER_VALUE.fullmatch(name) or any(c.name == name for c in clusters):
            continue
        clusters.append(ClusterRef(name, name, str(item.get("location") or ""), str(item.get("resourceGroup") or ""),
                                   str(item.get("provisioningState") or "")))
    return clusters


def list_clusters(scope, run):
    s = clean_scope(scope)
    argv = ["az", "aks", "list", "--output", "json"] + (["--resource-group", s["resource_group"]] if s["resource_group"] else []) + (
        ["--subscription", s["subscription"]] if s["subscription"] else [])
    return parse(run(argv))


def cluster_credentials(cluster, scope, target, run):
    s = clean_scope(scope)
    argv = ["az", "aks", "get-credentials", "--name", cluster.name, "--file", str(target)]
    argv += ["--resource-group", cluster.group] if cluster.group else []
    run(argv + (["--subscription", s["subscription"]] if s["subscription"] else []))
