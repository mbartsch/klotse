"""Read-only AKS metadata and public Azure portal links; no Azure API calls."""
import re
from urllib.parse import quote

_RESOURCE = re.compile(
    r'azure://(/subscriptions/[A-Za-z0-9-]+/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft\.Compute/'
    r'(?:virtualMachines/[A-Za-z0-9_.-]+|virtualMachineScaleSets/[A-Za-z0-9_.-]+/virtualMachines/[0-9]+))', re.I)


def portal_url(raw):
    provider = (raw.get('spec') or {}).get('providerID')
    if not isinstance(provider, str) or len(provider) > 2048:
        return None
    match = _RESOURCE.fullmatch(provider)
    return 'https://portal.azure.com/#resource' + quote(match[1], safe='/') if match else None


def node_values(raw):
    labels = (raw.get('metadata') or {}).get('labels') or {}
    priority = labels.get('kubernetes.azure.com/priority') or labels.get('kubernetes.azure.com/scalesetpriority') or ''
    capacity = {'spot': 'Spot', 'regular': 'Regular'}.get(str(priority).lower(), '')
    return {'machine-type': labels.get('node.kubernetes.io/instance-type') or labels.get('beta.kubernetes.io/instance-type') or '',
            'capacity': capacity,
            'node-image': labels.get('kubernetes.azure.com/node-image-version') or '',
            'region': labels.get('topology.kubernetes.io/region') or ''}
