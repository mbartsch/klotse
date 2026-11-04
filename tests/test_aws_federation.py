import json
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

from klotse.plugins import ActionRequest, Message, OpenUrl, ResourceInfo
from klotse_plugins.aws_nodes import AwsNodes
from klotse_plugins.aws_nodes import federation
from klotse_plugins.aws_nodes.ec2 import Ec2Error, Ec2Unavailable
from klotse_plugins.aws_nodes.federation import Credentials, FederationError, console_credentials, login_url

ARN = "arn:aws:eks:eu-west-1:123456789012:cluster/prod"
SECRET = "SECRETKEYVALUE+/abc"
PLAIN = "https://eu-west-1.console.aws.amazon.com/ec2/home?region=eu-west-1#InstanceDetails:instanceId=i-0abc123"


def node(name="n1", instance="i-0abc123", zone="eu-west-1a"):
    labels = {"node.kubernetes.io/instance-type": "m5.large", "topology.kubernetes.io/region": zone[:-1]}
    return ResourceInfo(name, "", {"metadata": {"name": name, "uid": "u", "labels": labels},
                                   "spec": {"providerID": f"aws:///{zone}/{instance}"}})


class FakeSts:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def assume_role(self, **kwargs):
        self.calls.append(("assume_role", kwargs))
        if self.fail:
            raise Exception(self.fail)
        return {"Credentials": {"AccessKeyId": "ASIAROLE", "SecretAccessKey": "rolesecret", "SessionToken": "roletoken"}}

    def get_federation_token(self, **kwargs):
        self.calls.append(("get_federation_token", kwargs))
        if self.fail:
            raise Exception(self.fail)
        return {"Credentials": {"AccessKeyId": "ASIAFED", "SecretAccessKey": "fedsecret", "SessionToken": "fedtoken"}}


class FakeSession:
    def __init__(self, token="sessiontoken", sts=None, credentials=True):
        self.sts = sts or FakeSts()
        frozen = SimpleNamespace(access_key="AKIABASE", secret_key=SECRET, token=token)
        self._credentials = SimpleNamespace(get_frozen_credentials=lambda: frozen) if credentials else None

    def get_credentials(self):
        return self._credentials

    def client(self, name):
        assert name == "sts"
        return self.sts


class FakeSignin:
    """The federation endpoint: remembers what it was sent and answers with a token."""

    def __init__(self, answer=None, error=None):
        self.sent, self.answer, self.error = [], answer if answer is not None else {"SigninToken": "TOKEN/+="}, error

    def __call__(self, endpoint, form):
        self.sent.append((endpoint, form))
        if self.error:
            raise self.error
        return self.answer


# ---- the login URL ------------------------------------------------------------------------
def test_the_token_is_requested_by_post_with_the_session_and_the_login_url_carries_it():
    signin = FakeSignin()
    url = login_url(Credentials("ASIA1", "secret1", "token1"), PLAIN, fetch=signin)
    (endpoint, form), = signin.sent
    assert endpoint == "https://signin.aws.amazon.com/federation" and form["Action"] == "getSigninToken"
    assert json.loads(form["Session"]) == {"sessionId": "ASIA1", "sessionKey": "secret1", "sessionToken": "token1"}
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == endpoint
    assert query == {"Action": ["login"], "Issuer": [federation.ISSUER], "Destination": [PLAIN], "SigninToken": ["TOKEN/+="]}
    assert "secret1" not in url and "token1" not in url.replace("SigninToken", "")  # the credentials are not in the URL


def test_govcloud_uses_its_own_sign_in_endpoint():
    signin = FakeSignin()
    url = login_url(Credentials("a", "b", "c"), PLAIN, gov=True, fetch=signin)
    assert signin.sent[0][0] == "https://signin.amazonaws-us-gov.com/federation"
    assert url.startswith("https://signin.amazonaws-us-gov.com/federation?Action=login")


def test_a_missing_token_is_an_error():
    for answer in ({}, {"SigninToken": ""}, ["x"]):
        with pytest.raises(FederationError, match="did not return a sign-in token"):
            login_url(Credentials("a", "b", "c"), PLAIN, fetch=FakeSignin(answer=answer))


def test_fetch_posts_a_form_and_never_puts_the_credentials_in_an_error(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b'{"SigninToken": "t"}'

    def urlopen(request, timeout):
        seen.update(method=request.get_method(), url=request.full_url, body=request.data.decode(), timeout=timeout)
        return Response()

    monkeypatch.setattr(federation.urllib.request, "urlopen", urlopen)
    assert federation.fetch_json("https://signin.aws.amazon.com/federation", {"Action": "getSigninToken", "Session": SECRET}) == {"SigninToken": "t"}
    assert seen["method"] == "POST" and "Session=" in seen["body"] and SECRET.split("/")[0] not in seen["url"]

    def fail_http(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(federation.urllib.request, "urlopen", fail_http)
    with pytest.raises(FederationError) as http:
        federation.fetch_json("https://signin.aws.amazon.com/federation", {"Session": SECRET})
    assert str(http.value) == "AWS sign-in answered HTTP 400"

    def fail_other(request, timeout):
        raise ValueError(f"bad request {request.data!r} for {request.full_url}")  # such texts can quote the request

    monkeypatch.setattr(federation.urllib.request, "urlopen", fail_other)
    with pytest.raises(FederationError) as other:
        federation.fetch_json("https://signin.aws.amazon.com/federation", {"Session": SECRET})
    assert str(other.value) == "could not reach the AWS sign-in service (ValueError)" and "SECRET" not in str(other.value)


# ---- which credentials ------------------------------------------------------------------------------
def test_temporary_profile_credentials_are_used_as_they_are():
    session = FakeSession(token="sessiontoken")
    assert console_credentials(session) == Credentials("AKIABASE", SECRET, "sessiontoken")
    assert session.sts.calls == []  # an IAM Identity Center or assumed-role profile needs no STS call


def test_long_term_keys_go_through_a_federation_token():
    session = FakeSession(token=None)
    assert console_credentials(session) == Credentials("ASIAFED", "fedsecret", "fedtoken")
    (name, kwargs), = session.sts.calls
    assert name == "get_federation_token" and kwargs["Name"] == "klotse-console" and kwargs["DurationSeconds"] == 3600
    assert json.loads(kwargs["Policy"])["Statement"][0] == {"Effect": "Allow", "Action": "*", "Resource": "*"}


def test_several_accounts_assume_the_role_in_the_clusters_account():
    session = FakeSession()
    assert console_credentials(session, "123456789012", "Ops/Deploy") == Credentials("ASIAROLE", "rolesecret", "roletoken")
    assert session.sts.calls == [("assume_role", {"RoleArn": "arn:aws:iam::123456789012:role/Ops/Deploy", "RoleSessionName": "klotse-console"})]
    gov = FakeSession()
    console_credentials(gov, "210987654321", "R", gov=True)
    assert gov.sts.calls[0][1]["RoleArn"] == "arn:aws-us-gov:iam::210987654321:role/R"


def test_credential_problems_are_readable_and_hold_no_secret():
    with pytest.raises(FederationError, match="no AWS credentials were found"):
        console_credentials(FakeSession(credentials=False))
    with pytest.raises(FederationError) as assume:
        console_credentials(FakeSession(sts=FakeSts(fail="AccessDenied: not authorized to assume\nrequest id 123")), "123456789012", "R")
    assert str(assume.value) == "could not assume the role R in account 123456789012: AccessDenied: not authorized to assume"
    with pytest.raises(FederationError, match="could not get a federation token: boom"):
        console_credentials(FakeSession(token=None, sts=FakeSts(fail="boom")))
    for error in (assume.value,):
        assert SECRET not in str(error)


# ---- the plugin action ----------------------------------------------------------------------------------
def make_plugin(session=None, signin=None, error=None):
    plugin = AwsNodes()
    plugin.federation_fetch = signin or FakeSignin()

    def factory(profile):
        factory.profiles.append(profile)
        if error:
            raise error
        return session or FakeSession()

    factory.profiles = []
    plugin.session_factory = factory
    return plugin, factory


def run(plugin, context=ARN, **settings):
    values = {"multi_account": False, "sso_start_url": "", "role_name": "", "account_map": "", "auto_login": True,
              "use_aws_api": False, "aws_profile": "work", **settings}
    return plugin.run(ActionRequest("aws-nodes", "console", "Node", context, [node()], values))


def test_automatic_sign_in_opens_the_one_time_login_link():
    plugin, factory = make_plugin()
    [result] = run(plugin)
    assert isinstance(result, OpenUrl) and result.url.startswith("https://signin.aws.amazon.com/federation?Action=login")
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(result.url).query)["Destination"] == [PLAIN]
    assert factory.profiles == ["work"]  # the plugin's AWS profile setting is the one used


def test_it_is_off_unless_asked_for():
    plugin, factory = make_plugin()
    assert run(plugin, auto_login=False) == [OpenUrl(PLAIN)] and factory.profiles == []


def test_several_accounts_assume_the_named_role_and_sign_in_to_that_account():
    session = FakeSession()
    plugin, _ = make_plugin(session=session)
    [result] = run(plugin, multi_account=True, role_name="Admin")
    assert isinstance(result, OpenUrl) and "federation?Action=login" in result.url
    assert session.sts.calls[0][1]["RoleArn"] == "arn:aws:iam::123456789012:role/Admin"


def test_several_accounts_need_the_account_and_a_role_else_the_normal_link_with_a_reason():
    plugin, factory = make_plugin()
    warning, *rest = run(plugin, multi_account=True)  # no role name
    assert isinstance(warning, Message) and warning.level == "warning" and "role name" in warning.text and factory.profiles == []
    warning, *rest = run(plugin, context="renamed", multi_account=True, role_name="R")
    assert "account" in warning.text and any(isinstance(r, Message) and "renamed=123456789012" in r.text for r in rest)


def test_identity_center_keeps_its_own_portal_link():
    plugin, factory = make_plugin()
    [result] = run(plugin, multi_account=True, role_name="Admin", sso_start_url="https://d-1.awsapps.com/start")
    assert result.url.startswith("https://d-1.awsapps.com/start/#/console?account_id=123456789012") and factory.profiles == []


def test_without_boto3_or_when_aws_says_no_the_normal_link_opens_with_a_warning():
    plugin, _ = make_plugin(error=Ec2Unavailable("boto3 is not installed"))
    warning, link = run(plugin)
    assert "needs boto3" in warning.text and link == OpenUrl(PLAIN)
    plugin, _ = make_plugin(error=Ec2Error("The config profile (work) could not be found"))
    warning, link = run(plugin)
    assert "Automatic sign-in did not work: The config profile (work) could not be found" in warning.text and link == OpenUrl(PLAIN)
    plugin, _ = make_plugin(signin=FakeSignin(error=FederationError("AWS sign-in answered HTTP 400")))
    warning, link = run(plugin)
    assert "AWS sign-in answered HTTP 400" in warning.text and link == OpenUrl(PLAIN)
    plugin, _ = make_plugin(session=FakeSession(credentials=False))
    assert "no AWS credentials were found" in run(plugin)[0].text


def test_the_login_link_is_never_in_a_message_or_the_credentials_in_the_url():
    plugin, _ = make_plugin(session=FakeSession(token=None))
    [result] = run(plugin)
    assert "fedsecret" not in result.url and "fedtoken" not in result.url.replace("SigninToken", "")
    plugin, _ = make_plugin(signin=FakeSignin(error=FederationError("AWS sign-in answered HTTP 400")))
    for item in run(plugin):
        if isinstance(item, Message):
            assert "federation?Action" not in item.text and "SigninToken" not in item.text
