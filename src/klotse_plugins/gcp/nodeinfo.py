"""Read-only GKE node metadata and validated Compute Engine console addresses."""
import re
from urllib.parse import quote, urlencode


_PROVIDER = re.compile(
    r"gce://([a-z][a-z0-9-]{4,28}[a-z0-9])/([a-z]+[0-9]*-[a-z]+[0-9]+-[a-z])/"
    r"([a-z](?:[a-z0-9-]{0,61}[a-z0-9])?)"
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def instance_ref(raw):
    """Accept only a complete gce://project/zone/instance, never a URL authority."""
    provider = _mapping(_mapping(raw).get("spec")).get("providerID")
    match = _PROVIDER.fullmatch(provider) if isinstance(provider, str) else None
    return match.groups() if match else None


def console_url(raw):
    ref = instance_ref(raw)
    if ref is None:
        return None
    project, zone, instance = ref
    return ("https://console.cloud.google.com/compute/instancesDetail/zones/"
            f"{quote(zone, safe='')}/instances/{quote(instance, safe='')}?{urlencode({'project': project})}")


def node_values(raw):
    """Unknown metadata stays empty; no pricing or cloud API lookups are made."""
    raw = _mapping(raw)
    labels = _mapping(_mapping(raw.get("metadata")).get("labels"))
    info = _mapping(_mapping(raw.get("status")).get("nodeInfo"))

    def text(value):
        return value if isinstance(value, str) else ""

    capacity = ""
    if labels.get("cloud.google.com/gke-spot") == "true":
        capacity = "Spot"
    elif labels.get("cloud.google.com/gke-preemptible") == "true":
        capacity = "Preemptible"
    elif labels.get("cloud.google.com/gke-provisioning") in ("spot", "preemptible", "standard"):
        capacity = labels["cloud.google.com/gke-provisioning"].capitalize()
    elif labels.get("cloud.google.com/gke-spot") == "false" and labels.get("cloud.google.com/gke-preemptible") == "false":
        capacity = "Standard"
    return {
        "machine_type": text(labels.get("node.kubernetes.io/instance-type")) or text(labels.get("beta.kubernetes.io/instance-type")),
        "capacity": capacity,
        "image": text(info.get("osImage")) or text(labels.get("cloud.google.com/gke-os-distribution")),
    }
