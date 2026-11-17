import pytest
from klotse.plugins.api import EnrichRequest, ActionRequest, ResourceInfo, Message, OpenUrl
from klotse_plugins.azure import AzurePlugin
from klotse_plugins.azure.metadata import portal_url, node_values

VM = 'azure:///subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/demo/providers/Microsoft.Compute/virtualMachines/node1'
VMSS = VM.rsplit('virtualMachines/', 1)[0] + 'virtualMachineScaleSets/pool/virtualMachines/0'

def raw(provider=VM, labels=None):
    return {'spec': {'providerID': provider}, 'metadata': {'labels': labels or {}}}

@pytest.mark.parametrize('provider', [VM, VMSS])
def test_portal_uses_fixed_https_host_and_validated_resource_id(provider):
    assert portal_url(raw(provider)) == 'https://portal.azure.com/#resource' + provider[len('azure://'):]

@pytest.mark.parametrize('provider', ['', None, 42, 'azure://evil.example/x', VM+'?redirect=https://evil.example', VM+'#bad', VM+'/../other', VM+'%2fother', 'gce://project/zone/node'])
def test_untrusted_provider_ids_do_not_make_links(provider):
    assert portal_url(raw(provider)) is None


def test_metadata_has_no_guessed_price_or_capacity():
    assert node_values(raw()) == {'machine-type': '', 'capacity': '', 'node-image': '', 'region': ''}
    values = node_values(raw(labels={'node.kubernetes.io/instance-type': 'Standard_D4s_v5',
                                    'kubernetes.azure.com/scalesetpriority': 'spot',
                                    'kubernetes.azure.com/node-image-version': 'AKSUbuntu-2204',
                                    'topology.kubernetes.io/region': 'eastus'}))
    assert values == {'machine-type': 'Standard_D4s_v5', 'capacity': 'Spot', 'node-image': 'AKSUbuntu-2204', 'region': 'eastus'}


def test_enrichment_is_node_only_and_skips_other_clouds():
    plugin = AzurePlugin()
    resources = [ResourceInfo('azure-node', '', raw()), ResourceInfo('other', '', raw('gce://project/zone/node'))]
    result = plugin.enrich(EnrichRequest('azure', 'Node', 'ctx', resources, {}))
    assert set(result) == {'azure-node'}
    assert plugin.enrich(EnrichRequest('azure', 'Pod', 'ctx', resources, {})) == {}


def test_portal_action_handles_missing_and_single_node():
    plugin = AzurePlugin()
    assert isinstance(plugin.run(ActionRequest('azure', 'portal', 'Node', 'ctx', [], {})), Message)
    result = plugin.run(ActionRequest('azure', 'portal', 'Node', 'ctx', [ResourceInfo('n', '', raw())], {}))
    assert isinstance(result, OpenUrl)
    assert 'WIP' in plugin.name and 'help appreciated' in plugin.description
