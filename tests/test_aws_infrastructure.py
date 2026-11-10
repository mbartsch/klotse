from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from klotse.plugins import EnrichRequest, ResourceInfo
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.aws.awscreds import AwsCredentials, CredentialsError
from klotse_plugins.aws.infrastructure import InfrastructureApi, console_url, lb_name, load_balancer_refs, volume_ref
from klotse_plugins.aws.ec2 import Ec2Unavailable


def test_volume_identifiers_and_region_are_read_from_pv_topology():
    pv = {"spec": {"csi": {"driver": "ebs.csi.aws.com", "volumeHandle": "vol-0123abcd"}},
          "metadata": {"labels": {"topology.kubernetes.io/zone": "eu-west-1b"}}}
    assert volume_ref(pv) == ("eu-west-1", "vol-0123abcd")
    assert volume_ref({"spec": {"awsElasticBlockStore": {"volumeID": "aws://us-east-2a/vol-abcdef"}}}) == (
        "us-east-2", "vol-abcdef")
    assert volume_ref({"spec": {"csi": {"driver": "other.csi", "volumeHandle": "vol-0123abcd"}}}) is None
    assert volume_ref({"spec": {"csi": {"driver": "ebs.csi.aws.com", "volumeHandle": "vol-malformed"}}}) is None


@pytest.mark.parametrize("host, expected", [
    ("app-123.eu-west-1.elb.amazonaws.com", [("eu-west-1", "app-123.eu-west-1.elb.amazonaws.com")]),
    ("dualstack.internal-app-123.us-east-1.elb.amazonaws.com", [("us-east-1", "internal-app-123.us-east-1.elb.amazonaws.com")]),
    ("customer.example.com", []),
])
def test_load_balancer_dns_is_restricted_to_aws_elb(host, expected):
    raw = {"status": {"loadBalancer": {"ingress": [{"hostname": host}]}}}
    assert load_balancer_refs(raw) == expected


def test_load_balancer_name_and_console_link_are_escaped():
    assert lb_name("internal-k8s-api-1234567890.us-west-2.elb.amazonaws.com") == "k8s-api"
    assert "Volumes:volumeId=vol-abc" in console_url("eu-west-1", "volume", "vol-abc")
    assert "%2F" in console_url("eu-west-1", "load-balancer", "arn:aws:elasticloadbalancing:eu-west-1:1:loadbalancer/app")


def test_elbv2_dualstack_response_matches_normalized_dns_name():
    api = InfrastructureApi("default")
    api.call = lambda *_args, **_kwargs: ElementTree.fromstring("""
        <DescribeLoadBalancersResponse><LoadBalancers><member>
        <LoadBalancerArn>arn:aws:elasticloadbalancing:eu-west-1:123:loadbalancer/app/api/123</LoadBalancerArn>
        <DNSName>dualstack.api-123.eu-west-1.elb.amazonaws.com</DNSName><Type>application</Type>
        <State><Code>active</Code></State><Scheme>internet-facing</Scheme><VpcId>vpc-1</VpcId>
        </member></LoadBalancers></DescribeLoadBalancersResponse>""")
    answer = api.load_balancer("eu-west-1", "api-123.eu-west-1.elb.amazonaws.com")
    assert answer["type"] == "application" and answer["state"] == "active"


def test_infrastructure_uses_the_same_optional_boto3_credentials_as_node_lookups():
    class Provider:
        def get_frozen_credentials(self):
            return SimpleNamespace(access_key="AKIA_TEST", secret_key="secret", token="session")

    session = SimpleNamespace(get_credentials=lambda: Provider())
    api = InfrastructureApi("work", session_factory=lambda profile: session)
    assert api._credentials() == AwsCredentials("AKIA_TEST", "secret", "session")

    api = InfrastructureApi("work", session_factory=lambda _profile: (_ for _ in ()).throw(Ec2Unavailable()))
    api.source = SimpleNamespace(get=lambda: AwsCredentials("AKIA_CLI", "secret"))
    assert api._credentials() == AwsCredentials("AKIA_CLI", "secret")


def test_pvc_uses_exact_bound_pv_and_checks_claim_uid(monkeypatch):
    class FakeCore:
        def __init__(self, client):
            assert isinstance(client, FakeClient)

        def read_persistent_volume(self, name):
            assert name == "pv-1"
            return {"spec": {"csi": {"driver": "ebs.csi.aws.com", "volumeHandle": "vol-0123abcd"},
                             "claimRef": {"name": "data", "namespace": "prod", "uid": "pvc-uid"}},
                    "metadata": {"labels": {"topology.kubernetes.io/zone": "eu-west-1a"}}}

    class FakeClient:
        def sanitize_for_serialization(self, value):
            return value

    monkeypatch.setattr("kubernetes.client.CoreV1Api", FakeCore)
    plugin = AwsPlugin()
    plugin._pv_manifests = {}
    plugin._lock = __import__("threading").Lock()
    pvc = ResourceInfo("data", "prod", {"metadata": {"uid": "pvc-uid"}, "spec": {"volumeName": "pv-1"}})
    request = EnrichRequest("aws", "PersistentVolumeClaim", "cluster", [pvc], {}, api_client=lambda: FakeClient())
    assert plugin._infrastructure_refs(request, pvc) == [("eu-west-1", "vol-0123abcd", "volume")]

    other = ResourceInfo("data", "prod", {"metadata": {"uid": "replacement"}, "spec": {"volumeName": "pv-1"}})
    assert plugin._infrastructure_refs(request, other) == []


def test_enrichment_returns_load_balancer_metadata_without_wide_scans(monkeypatch):
    plugin = AwsPlugin()
    plugin._infrastructure = SimpleNamespace(lookup=lambda profile, region, identifier, kind: {
        "id": "arn:aws:elasticloadbalancing:eu-west-1:123:loadbalancer/app/api/123", "type": "application",
        "state": "active", "scheme": "internet-facing"})
    resource = ResourceInfo("api", "prod", {"status": {"loadBalancer": {"ingress": [
        {"hostname": "api-123.eu-west-1.elb.amazonaws.com"}]}}})
    request = EnrichRequest("aws", "Ingress", "cluster", [resource], {})
    result = plugin.enrich(request)
    assert result["prod/api"]["aws_state"] == "active"
    assert result["prod/api"]["aws_region"] == "eu-west-1"
    assert "internet-facing" in result["prod/api"]["aws_details"]


def test_credential_failures_keep_actionable_reason_for_plugin_status(qapp):
    plugin = AwsPlugin()
    plugin._infrastructure = SimpleNamespace(lookup=lambda *_args: (_ for _ in ()).throw(
        CredentialsError("AWS CLI profile 'work' needs aws sso login")))
    resource = ResourceInfo("api", "prod", {"status": {"loadBalancer": {"ingress": [
        {"hostname": "api-123.eu-west-1.elb.amazonaws.com"}]}}})
    request = EnrichRequest("aws", "Ingress", "cluster", [resource], {})
    assert plugin.enrich(request) == {}
    assert len(request.warnings) == 1
    assert "CredentialsError: AWS CLI profile 'work' needs aws sso login" in request.warnings[0]
