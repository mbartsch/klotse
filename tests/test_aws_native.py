import json
import subprocess
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.plugins import ActionRequest, EnrichRequest, ResourceInfo
from klotse.plugins.manager import PluginManager
from klotse_plugins.aws_nodes import AwsNodes, console, pricing
from klotse_plugins.aws_nodes import ec2 as ec2_module
from klotse_plugins.aws_nodes import native as native_module
from klotse_plugins.aws_nodes.awscreds import (REFRESH_BEFORE_S, AwsCredentials, CredentialSource, CredentialsError)
from klotse_plugins.aws_nodes.ec2 import Ec2, Ec2Error, Ec2Unavailable, default_ec2
from klotse_plugins.aws_nodes.native import AwsHttpError, NativeEc2Client
from klotse_plugins.aws_nodes.sigv4 import sign

NS = 'xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"'


# ---- the signature --------------------------------------------------------------------------
def test_the_signature_has_the_shape_aws_expects_and_covers_the_token():
    headers = sign("POST", "https://ec2.eu-west-1.amazonaws.com/", {"Content-Type": "application/x-www-form-urlencoded"},
                   b"Action=DescribeInstances", "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "eu-west-1", "ec2",
                   "20260102T030405Z", "SESSION")
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260102/eu-west-1/ec2/aws4_request, ")
    assert "SignedHeaders=content-type;host;x-amz-date;x-amz-security-token, Signature=" in auth
    assert headers["X-Amz-Date"] == "20260102T030405Z" and headers["X-Amz-Security-Token"] == "SESSION"
    assert len(auth.rsplit("Signature=", 1)[1]) == 64
    plain = sign("POST", "https://ec2.eu-west-1.amazonaws.com/", {}, b"", "A", "S", "eu-west-1", "ec2", "20260102T030405Z")
    assert "X-Amz-Security-Token" not in plain and "SignedHeaders=host;x-amz-date," in plain["Authorization"]


def test_the_signature_matches_a_known_good_value():
    # Computed with botocore's own SigV4Auth for exactly this request and clock (see the commit that added sigv4.py).
    headers = sign("POST", "https://ec2.eu-west-1.amazonaws.com/", {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                   b"Action=DescribeInstances&InstanceId.1=i-0abc&Version=2016-11-15", "AKIDEXAMPLE",
                   "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "eu-west-1", "ec2", "20260102T030405Z")
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260102/eu-west-1/ec2/aws4_request, SignedHeaders=content-type;host;x-amz-date, "
        "Signature=" + KNOWN_SIGNATURE)


KNOWN_SIGNATURE = "f8ab29fb6dbb89f4ddaef82de698a1e7ba2f6f7302fe5715cdcbb573504d0501"


# ---- credentials -------------------------------------------------------------------------------
def completed(stdout="", stderr="", code=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def process_json(expiration=None, token="tok"):
    data = {"Version": 1, "AccessKeyId": "ASIA1", "SecretAccessKey": "secret1", "SessionToken": token}
    if expiration:
        data["Expiration"] = expiration
    return json.dumps(data)


def test_environment_variables_are_used_when_no_profile_is_named():
    env = {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK", "AWS_SESSION_TOKEN": "TK"}
    ran = []
    source = CredentialSource("", run=lambda *a, **k: ran.append(a), which=lambda n: "aws", environ=env)
    assert source.get() == AwsCredentials("AK", "SK", "TK") and ran == []


def test_a_named_profile_asks_the_cli_even_when_variables_are_set():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(process_json())

    env = {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK", "PATH": "x"}
    source = CredentialSource("prod", run=run, which=lambda n: "/usr/bin/aws", environ=env)
    credentials = source.get()
    assert credentials == AwsCredentials("ASIA1", "secret1", "tok", 0.0)
    (command, kwargs), = calls
    assert command == ["/usr/bin/aws", "configure", "export-credentials", "--format", "process", "--profile", "prod"]
    assert kwargs["timeout"] == 30 and kwargs["stdin"] == subprocess.DEVNULL and kwargs["env"] == env


def test_without_a_profile_or_variables_the_clis_default_credentials_are_used():
    calls = []
    source = CredentialSource("", run=lambda command, **k: calls.append(command) or completed(process_json()), which=lambda n: "aws", environ={})
    assert source.get().access_key == "ASIA1"
    assert calls == [["aws", "configure", "export-credentials", "--format", "process"]]


def test_credentials_are_reused_until_shortly_before_they_expire_and_static_keys_for_good():
    clock = [1_000_000.0]
    expiry = datetime.fromtimestamp(clock[0] + 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = []
    source = CredentialSource("p", run=lambda c, **k: calls.append(c) or completed(process_json(expiry)),
                              which=lambda n: "aws", environ={}, now=lambda: clock[0])
    source.get()
    clock[0] += 3600 - REFRESH_BEFORE_S - 5
    source.get()
    assert len(calls) == 1  # still good
    clock[0] += 10
    source.get()
    assert len(calls) == 2  # five minutes before it runs out: asked again
    static = []
    never = CredentialSource("p", run=lambda c, **k: static.append(c) or completed(process_json(None, "")), which=lambda n: "aws",
                             environ={}, now=lambda: clock[0])
    never.get()
    clock[0] += 10 ** 7
    never.get()
    assert len(static) == 1  # no expiry: kept


def test_credential_problems_say_what_to_do():
    with pytest.raises(CredentialsError, match="install the AWS CLI"):
        CredentialSource("", which=lambda n: None, environ={}).get()
    failing = lambda c, **k: completed("", "x\nThe SSO session associated with this profile has expired or is otherwise invalid.", 255)  # noqa: E731
    with pytest.raises(CredentialsError, match="for the profile work: The SSO session associated with this profile has expired"):
        CredentialSource("work", run=failing, which=lambda n: "aws", environ={}).get()
    with pytest.raises(CredentialsError, match="2.9 or newer"):
        CredentialSource("p", run=lambda c, **k: completed("not json"), which=lambda n: "aws", environ={}).get()

    def broken(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)

    with pytest.raises(CredentialsError, match="could not run the AWS CLI"):
        CredentialSource("p", run=broken, which=lambda n: "aws", environ={}).get()


# ---- the client -----------------------------------------------------------------------------------
class Credentials:
    def __init__(self, token="TOKEN"):
        self.value = AwsCredentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", token)

    def get(self):
        return self.value


class Http:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, url, headers, body):
        self.sent.append((url, headers, body.decode()))
        return self.answers.pop(0)


DESCRIBE = f"""<DescribeInstancesResponse {NS}><requestId>r</requestId><reservationSet><item><instancesSet>
<item><instanceId>i-0aaa</instanceId><imageId>ami-1</imageId><launchTime>2026-11-04T08:55:45.000Z</launchTime>
<placement><availabilityZone>eu-west-1a</availabilityZone></placement><instanceLifecycle>spot</instanceLifecycle></item>
<item><instanceId>i-0bbb</instanceId><imageId>ami-2</imageId><launchTime>2026-01-01T00:00:00.000Z</launchTime>
<placement><availabilityZone>eu-west-1b</availabilityZone></placement></item></instancesSet></item></reservationSet>
</DescribeInstancesResponse>""".encode()


def client(*answers, token="TOKEN"):
    http = Http(*answers)
    return NativeEc2Client("eu-west-1", Credentials(token), http=http, now=lambda: 1_767_323_045.0), http


def test_describe_instances_is_signed_posted_and_answered_like_boto3():
    c, http = client((200, DESCRIBE))
    answer = c.describe_instances(["i-0aaa", "i-0bbb"])
    (url, headers, body), = http.sent
    assert url == "https://ec2.eu-west-1.amazonaws.com/"
    assert body == "Action=DescribeInstances&InstanceId.1=i-0aaa&InstanceId.2=i-0bbb&Version=2016-11-15"
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260102/eu-west-1/ec2/aws4_request")
    assert headers["X-Amz-Security-Token"] == "TOKEN" and headers["X-Amz-Date"] == "20260102T030405Z"
    first, second = answer["Reservations"][0]["Instances"]
    assert (first["InstanceId"], first["ImageId"], first["InstanceLifecycle"]) == ("i-0aaa", "ami-1", "spot")
    assert first["LaunchTime"] == datetime(2026, 11, 4, 8, 55, 45, tzinfo=timezone.utc)
    assert first["Placement"] == {"AvailabilityZone": "eu-west-1a"} and second["InstanceLifecycle"] is None


def test_spot_price_history_follows_next_token_and_reads_each_entry():
    page = lambda items, token="": (200, (f"<DescribeSpotPriceHistoryResponse {NS}><spotPriceHistorySet>" + "".join(  # noqa: E731
        f"<item><instanceType>{t}</instanceType><productDescription>Linux/UNIX</productDescription><spotPrice>{p}</spotPrice>"
        f"<timestamp>2026-11-04T08:00:00.000Z</timestamp><availabilityZone>{z}</availabilityZone></item>" for t, p, z in items)
        + f"</spotPriceHistorySet><nextToken>{token}</nextToken></DescribeSpotPriceHistoryResponse>").encode())
    c, http = client(page([("m5.large", "0.031", "eu-west-1a")], "NEXT"), page([("m5.large", "0.040", "eu-west-1b")]))
    pages = list(c.get_paginator("describe_spot_price_history").paginate(
        InstanceTypes=["m5.large", "c5.large"], ProductDescriptions=["Linux/UNIX"], StartTime=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)))
    entries = [e for p in pages for e in p["SpotPriceHistory"]]
    assert [(e["InstanceType"], e["AvailabilityZone"], e["SpotPrice"]) for e in entries] == [
        ("m5.large", "eu-west-1a", "0.031"), ("m5.large", "eu-west-1b", "0.040")]
    first_body, second_body = http.sent[0][2], http.sent[1][2]
    assert "InstanceType.1=m5.large" in first_body and "InstanceType.2=c5.large" in first_body
    assert "ProductDescription.1=Linux%2FUNIX" in first_body and "StartTime=2026-01-02T03%3A04%3A05Z" in first_body
    assert "NextToken" not in first_body and "NextToken=NEXT" in second_body
    with pytest.raises(ValueError):
        c.get_paginator("describe_volumes")


def test_aws_error_answers_become_client_error_like_exceptions():
    error = b"<Response><Errors><Error><Code>InvalidInstanceID.NotFound</Code><Message>The instance ID 'i-0x' does not exist</Message></Error></Errors><RequestID>r</RequestID></Response>"
    c, _ = client((400, error))
    with pytest.raises(AwsHttpError) as caught:
        c.describe_instances(["i-0x"])
    assert caught.value.response["Error"]["Code"] == "InvalidInstanceID.NotFound" and "does not exist" in str(caught.value)
    with pytest.raises(AwsHttpError, match="AWS answered something unreadable"):
        client((502, b"<html>bad gateway"))[0].describe_instances(["i-0x"])
    with pytest.raises(AwsHttpError, match="HTTP 503"):
        client((503, b"<Response><Errors><Error><Code>X</Code></Error></Errors></Response>"))[0].describe_instances(["i-0x"])


def test_the_http_function_never_puts_the_request_in_an_error(monkeypatch):
    def refuse(request, timeout):
        raise ValueError(f"bad {request.data!r} {request.get_header('Authorization')}")

    monkeypatch.setattr(native_module.urllib.request, "urlopen", refuse)
    with pytest.raises(AwsHttpError) as caught:
        native_module.post("https://ec2.eu-west-1.amazonaws.com/", {"Authorization": "SECRETSIGNATURE"}, b"Action=X")
    assert str(caught.value) == "could not reach AWS (ValueError)" and "SECRET" not in str(caught.value)

    def http_error(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, __import__("io").BytesIO(b"<Response/>"))

    monkeypatch.setattr(native_module.urllib.request, "urlopen", http_error)
    assert native_module.post("https://ec2.eu-west-1.amazonaws.com/", {}, b"") == (403, b"<Response/>")
    assert native_module.endpoint("cn-north-1") == "https://ec2.cn-north-1.amazonaws.com.cn/"


def test_ec2_works_on_top_of_the_native_client_including_missing_instances():
    gone = b"<Response><Errors><Error><Code>InvalidInstanceID.NotFound</Code><Message>gone</Message></Error></Errors></Response>"
    # one call for both ids fails because one is gone; then each is asked on its own (the gone one fails again)
    c, http = client((400, gone), (200, DESCRIBE), (400, gone))
    details = Ec2(lambda region: c).instance_details("eu-west-1", ["i-0aaa", "i-0gone"])
    assert details["i-0aaa"]["spot"] is True and details["i-0aaa"]["ami"] == "ami-1" and "i-0gone" not in details
    assert len(http.sent) == 3


# ---- choosing boto3 or the built-in client -------------------------------------------------------------
def test_without_boto3_the_built_in_client_is_used_with_cli_credentials(monkeypatch):
    def no_boto(profile):
        raise Ec2Unavailable("no boto3")

    monkeypatch.setattr(ec2_module, "boto3_session", no_boto)
    made = []

    class Source:
        def __init__(self, profile):
            made.append(profile)

        def get(self):
            return AwsCredentials("a", "b", "c")

    monkeypatch.setattr("klotse_plugins.aws_nodes.awscreds.CredentialSource", Source)
    api = default_ec2("work")
    assert isinstance(api, Ec2) and made == ["work"]
    assert isinstance(api.client("eu-west-1"), NativeEc2Client)


def test_missing_credentials_fail_once_when_the_api_is_made(monkeypatch):
    monkeypatch.setattr(ec2_module, "boto3_session", lambda profile: (_ for _ in ()).throw(Ec2Unavailable("x")))

    class Source:
        def __init__(self, profile):
            pass

        def get(self):
            raise CredentialsError("no AWS credentials: install the AWS CLI")

    monkeypatch.setattr("klotse_plugins.aws_nodes.awscreds.CredentialSource", Source)
    with pytest.raises(Ec2Error, match="no AWS credentials"):
        default_ec2("")


def test_with_boto3_its_session_is_used(monkeypatch):
    made = []

    class Session:
        def client(self, name, region_name):
            made.append((name, region_name))
            return "boto-client"

    monkeypatch.setattr(ec2_module, "boto3_session", lambda profile: Session())
    assert default_ec2("p").client("us-east-1") == "boto-client" and made == [("ec2", "us-east-1")]


# ---- a profile for each cluster -----------------------------------------------------------------------------
def test_each_cluster_can_have_its_own_profile_and_the_rest_use_the_default():
    settings = {"aws_profile": "default-one", "profile_map": "prod-eu = prod-profile\narn:aws:eks:eu-west-1:123456789012:cluster/x=x-profile; staging=stg\nbad line\n=nameless\nempty="}
    assert console.profile_for("prod-eu", settings) == "prod-profile"
    assert console.profile_for("arn:aws:eks:eu-west-1:123456789012:cluster/x", settings) == "x-profile"
    assert console.profile_for("staging", settings) == "stg"
    assert console.profile_for("other", settings) == "default-one" and console.profile_for("", {}) == ""
    assert console.parse_pairs("a=1\nb=\n=c\nd=4", lambda v: v.isdigit()) == {"a": "1", "d": "4"}


def node(name, iid="i-0abc1", zone="eu-west-1a"):
    labels = {"node.kubernetes.io/instance-type": "m5.large", "topology.kubernetes.io/region": zone[:-1]}
    return ResourceInfo(name, "", {"metadata": {"name": name, "uid": "u", "labels": labels}, "spec": {"providerID": f"aws:///{zone}/{iid}"}})


@pytest.fixture
def prices(monkeypatch):
    monkeypatch.setattr("klotse_plugins.aws_nodes.on_demand_prices", lambda *a, **k: pricing.PriceTable({"m5.large": 0.096}))


def plugin_recording_profiles():
    plugin = AwsNodes()
    asked = []

    def api(profile):
        asked.append(profile)
        raise Ec2Error("denied")

    plugin._api = api
    return plugin, asked


SETTINGS = {"use_aws_api": True, "aws_profile": "default-one", "profile_map": "prod-eu=prod-profile"}


def test_the_clusters_profile_is_the_one_used_for_its_nodes(prices, tmp_path):
    plugin, asked = plugin_recording_profiles()
    plugin.enrich(EnrichRequest("aws-nodes", "Node", "prod-eu", [node("n1")], SETTINGS, cache_dir=tmp_path / "a"))
    plugin.enrich(EnrichRequest("aws-nodes", "Node", "other", [node("n1")], SETTINGS, cache_dir=tmp_path / "b"))
    assert asked == ["prod-profile", "default-one"]


def test_the_cost_estimate_uses_the_clusters_profile_too(prices, tmp_path):
    plugin, asked = plugin_recording_profiles()
    plugin.run(ActionRequest("aws-nodes", "cost", "Node", "prod-eu", [node("n1")], SETTINGS, cache_dir=tmp_path))
    assert asked == ["prod-profile"]


def test_the_automatic_sign_in_uses_the_clusters_profile(prices):
    plugin = AwsNodes()
    asked = []

    def factory(profile):
        asked.append(profile)
        raise Ec2Error("denied")

    plugin.session_factory = factory
    request = ActionRequest("aws-nodes", "console", "Node", "prod-eu", [node("n1")], {**SETTINGS, "auto_login": True})
    plugin.run(request)
    assert asked == ["prod-profile"]


def test_the_profile_is_part_of_the_setting_list_and_the_maps_are_multiline():
    kinds = {s.key: s.kind for s in AwsNodes.settings}
    assert kinds["profile_map"] == "multiline" and kinds["account_map"] == "multiline" and kinds["aws_profile"] == "text"
    assert AwsNodes.requires_api == 6
