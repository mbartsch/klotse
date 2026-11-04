from klotse.plugins import ActionRequest, OpenUrl
from klotse_plugins.aws_nodes import AwsNodes
from klotse_plugins.aws_nodes.awscreds import AwsCredentials
from klotse_plugins.aws_nodes.ec2 import Ec2Error
from klotse_plugins.aws_nodes.identity import native_account
from klotse_plugins.aws_nodes.nodeinfo import parse_node

OK = (b'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult>'
      b'<Arn>arn:aws:sts::222233334444:assumed-role/x/y</Arn><Account>222233334444</Account></GetCallerIdentityResult>'
      b'</GetCallerIdentityResponse>')


class Source:
    def get(self):
        return AwsCredentials("AKIDEXAMPLE", "secret", "token")


def test_the_account_comes_from_a_signed_get_caller_identity_call():
    seen = {}

    def http(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return 200, OK

    assert native_account(Source(), "eu-west-1", http) == "222233334444"
    assert seen["url"] == "https://sts.eu-west-1.amazonaws.com/" and b"Action=GetCallerIdentity" in seen["body"]
    assert "/eu-west-1/sts/aws4_request" in str(seen["headers"])


def test_sts_errors_become_readable_messages():
    error = b"<ErrorResponse><Error><Code>ExpiredToken</Code><Message>The security token is expired</Message></Error></ErrorResponse>"
    try:
        native_account(Source(), "eu-west-1", lambda *a: (403, error))
    except Ec2Error as caught:
        assert "expired" in str(caught)
    else:
        raise AssertionError("no error")


def node_resource():
    from klotse.plugins import ResourceInfo
    raw = {"metadata": {"name": "n", "labels": {"topology.kubernetes.io/region": "eu-west-1",
                                                 "node.kubernetes.io/instance-type": "m5.large"}},
           "spec": {"providerID": "aws:///eu-west-1a/i-0abc1"}}
    return ResourceInfo("n", "", raw)


def request(context, settings, logs):
    return ActionRequest("aws-nodes", "console", "Node", context, [node_resource()], settings, log=logs.append)


def test_the_console_asks_aws_for_the_account_only_when_nothing_else_knows_it():
    plugin, calls, logs = AwsNodes(), [], []
    plugin.account_lookup = staticmethod(lambda profile, region: calls.append((profile, region)) or "222233334444")
    info = parse_node(node_resource())
    base = {"multi_account": True, "sso_start_url": "https://d-1.awsapps.com/start", "role_name": "Admin",
            "cluster_profile": "prod"}
    results = plugin.open_console(request("minikube", base, logs), info)
    assert calls == [("prod", "eu-west-1")] and "account_id=222233334444" in results[0].url
    plugin.open_console(request("minikube", base, logs), info)
    assert len(calls) == 1  # kept per profile
    plugin.open_console(request("minikube", {**base, "cluster_account": "111111111111"}, logs), info)
    arn = "arn:aws:eks:eu-west-1:123456789012:cluster/x"
    results = plugin.open_console(request(arn, {**base, "cluster_profile": "other"}, logs), info)
    assert len(calls) == 1 and "account_id=123456789012" in results[0].url  # menu / ARN win, no lookup


def test_a_failed_lookup_falls_back_to_the_usual_message():
    plugin, logs = AwsNodes(), []

    def fail(profile, region):
        raise Ec2Error("expired")

    plugin.account_lookup = staticmethod(fail)
    results = plugin.open_console(request("minikube", {"multi_account": True}, logs), parse_node(node_resource()))
    assert "cannot tell which AWS account" in results[0].text and "expired" in logs[0]
