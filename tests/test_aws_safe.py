import pytest

from klotse.plugins import ResourceInfo
from klotse_plugins.aws import identity, native, safe
from klotse_plugins.aws.awscreds import AwsCredentials
from klotse_plugins.aws.ec2 import Ec2Error
from klotse_plugins.aws.nodeinfo import parse_node

HOSTILE = ["x.evil.example#", "eu-west-1.evil.example", "eu-west-1/../x", "EU-WEST-1", "eu-west-1\n", "", "a" * 40,
           "us-east-1@evil", "us-east-1:443", "eu west 1", "-1", "eu-west-"]


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-2", "us-gov-west-1", "cn-north-1", "ap-southeast-4", "eu-central-1"])
def test_real_region_names_are_accepted(region):
    assert safe.valid_region(region)


@pytest.mark.parametrize("region", HOSTILE)
def test_anything_else_is_not_a_region(region):
    assert not safe.valid_region(region)


def node(labels):
    raw = {"metadata": {"name": "n", "labels": labels}, "spec": {"providerID": "aws:///eu-west-1a/i-0abc1"}}
    return parse_node(ResourceInfo("n", "", raw))


def test_a_region_label_that_is_not_a_region_is_dropped():
    assert node({"topology.kubernetes.io/region": "eu-west-1"}).region == "eu-west-1"
    assert node({}).region == "eu-west-1"  # from the zone
    assert node({"topology.kubernetes.io/region": "x.evil.example#"}).region == ""


class Source:
    def get(self):
        return AwsCredentials("AKIDEXAMPLE", "secret", "session-token")


@pytest.mark.parametrize("region", HOSTILE[:4])
def test_a_hostile_region_never_sends_a_signed_request_anywhere(region):
    sent = []
    client = native.NativeEc2Client(region, Source(), http=lambda *a: sent.append(a))
    with pytest.raises(native.AwsHttpError):
        client.describe_instances(["i-0abc1"])
    with pytest.raises(Ec2Error):
        identity.native_account(Source(), region, http=lambda *a: sent.append(a))
    assert sent == []  # the session token never leaves for a host the label chose


def test_only_https_addresses_are_opened():
    assert safe.require_https("https://sts.eu-west-1.amazonaws.com/") == "https://sts.eu-west-1.amazonaws.com/"
    for bad in ("file:///etc/passwd", "http://example.com", "ftp://example.com", "//example.com"):
        with pytest.raises(ValueError):
            safe.require_https(bad)
    with pytest.raises(ValueError):
        native.post("file:///etc/passwd", {}, b"")


def test_xml_with_a_dtd_or_entities_is_refused_not_parsed():
    assert safe.parse_xml(b"<a><b>1</b></a>").find("b").text == "1"
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "aaaa"><!ENTITY y "&x;&x;&x;&x;">]><a>&y;</a>'
    for data in (bomb, b"<!doctype a><a/>", b"<a><!ENTITY x 'y'></a>"):
        with pytest.raises(safe.ParseError):
            safe.parse_xml(data)
    utf16_bomb = '<!DOCTYPE a [<!ENTITY x "expanded">]><a>&x;</a>'.encode("utf-16")
    with pytest.raises(safe.ParseError, match="unexpected DTD"):
        safe.parse_xml(utf16_bomb)
    utf32_bomb = '<!DOCTYPE a [<!ENTITY x "expanded">]><a>&x;</a>'.encode("utf-32")
    with pytest.raises(safe.ParseError, match="unexpected DTD"):
        safe.parse_xml(utf32_bomb)
