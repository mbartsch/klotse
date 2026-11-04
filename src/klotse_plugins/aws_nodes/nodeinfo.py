"""What the Node object itself says about an AWS instance (no credentials needed)."""
import re
from dataclasses import dataclass

PROVIDER_ID = re.compile(r"^aws:///(?P<zone>[^/]+)/(?P<id>i-[0-9a-f]+)$")  # Fargate and others do not match
REGION_PREFIX = re.compile(r"^([a-z]{2}(?:-gov)?-[a-z]+-\d+)")  # "us-east-1a" and "us-west-2-lax-1a" both give a region
CAPACITY_LABELS = ("karpenter.sh/capacity-type", "eks.amazonaws.com/capacityType")


@dataclass(frozen=True)
class NodeInfo:
    instance_id: str
    zone: str
    region: str
    instance_type: str  # "" when the node has no instance-type label
    capacity: str  # "on-demand", "spot" or "reserved"
    ami: str  # from the EKS node group image label; "" when the node does not say (Karpenter nodes)
    os: str  # "Linux" or "Windows", as the price lists name them


def parse_node(resource):
    """NodeInfo for a resource whose providerID is an EC2 instance, else None (other clouds, Fargate, no providerID)."""
    raw = resource.raw or {}
    match = PROVIDER_ID.match(str((raw.get("spec") or {}).get("providerID") or ""))
    if not match:
        return None
    labels = (raw.get("metadata") or {}).get("labels") or {}
    zone = match["zone"]
    region = labels.get("topology.kubernetes.io/region") or (REGION_PREFIX.match(zone) or [None, ""])[1]
    capacity = next((labels[key] for key in CAPACITY_LABELS if labels.get(key)), "").lower().replace("_", "-")
    system = (labels.get("kubernetes.io/os") or labels.get("beta.kubernetes.io/os") or "linux").lower()
    return NodeInfo(
        instance_id=match["id"], zone=zone, region=region,
        instance_type=labels.get("node.kubernetes.io/instance-type") or labels.get("beta.kubernetes.io/instance-type") or "",
        capacity=capacity if capacity in ("spot", "on-demand", "reserved") else "on-demand",
        ami=labels.get("eks.amazonaws.com/nodegroup-image") or "",
        os="Windows" if system == "windows" else "Linux")
