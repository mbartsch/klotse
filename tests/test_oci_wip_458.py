import pytest
from klotse.plugins.api import CloudError, Message
from klotse_plugins.oci import OciPlugin, oci_login_argv
from test_oci_sign_in import node, request

@pytest.mark.parametrize('region', ['--endpoint', 'us-phoenix-1;whoami', 'https://evil.example', 'us-phoenix-1\n--debug', ''])
def test_sign_in_builder_rejects_invalid_regions(region):
    with pytest.raises(CloudError):
        oci_login_argv(region, 'DEFAULT')

@pytest.mark.parametrize('profile', ['--debug', 'prod;whoami', 'bad\nprofile', '$(id)'])
def test_sign_in_builder_rejects_unsafe_profiles(profile):
    with pytest.raises(CloudError):
        oci_login_argv('us-phoenix-1', profile)


def test_invalid_region_is_reported_without_opening_terminal():
    plugin = OciPlugin()
    result = plugin.run(request(plugin, 'sign-in', [node('ocid1.instance.oc1.phx.abc', {'topology.kubernetes.io/region': '--endpoint'})]))
    assert isinstance(result, Message)
    assert result.level == 'warning'
    with pytest.raises(CloudError) as error:
        oci_login_argv('--endpoint', 'DEFAULT')
    assert result.text == str(error.value)


def test_invalid_profile_is_reported_without_opening_terminal():
    plugin = OciPlugin()
    result = plugin.run(request(plugin, 'sign-in', [node('ocid1.instance.oc1.phx.abc', {'topology.kubernetes.io/region': 'us-phoenix-1'})], {'oci_profile': '--debug'}))
    assert isinstance(result, Message)
    assert result.level == 'warning'
    with pytest.raises(CloudError) as error:
        oci_login_argv('us-phoenix-1', '--debug')
    assert result.text == str(error.value)


def test_wip_and_contribution_invitation_are_visible():
    assert 'WIP' in OciPlugin.name
    assert 'help appreciated' in OciPlugin.description

@pytest.mark.parametrize(('profile', 'expected'), [(None, 'DEFAULT'), ('', 'DEFAULT'), ('   ', 'DEFAULT'), (' OKE ', 'OKE')])
def test_profile_normalization_at_builder_and_action_boundaries(profile, expected):
    argv = oci_login_argv(' us-phoenix-1 ', profile)
    assert argv == ('oci', 'session', 'authenticate', '--region', 'us-phoenix-1', '--profile-name', expected)
    plugin = OciPlugin()
    resource = node('ocid1.instance.oc1.phx.abc', {'topology.kubernetes.io/region': 'us-phoenix-1'})
    result = plugin.run(request(plugin, 'sign-in', [resource], {'oci_profile': profile}))
    assert result.argv == argv
