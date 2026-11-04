"""An EC2 client that needs no boto3: it signs the two query API calls the plugin makes (DescribeInstances and
DescribeSpotPriceHistory) itself and answers in the same shape boto3's client does, so `ec2.Ec2` works with either."""
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .sigv4 import sign

API_VERSION = "2016-11-15"


class AwsHttpError(Exception):
    """An error answer from AWS, shaped like botocore's ClientError (`.response["Error"]["Code"]`)."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}" if code else message)
        self.response = {"Error": {"Code": code, "Message": message}}


def endpoint(region):
    return f"https://ec2.{region}.amazonaws.com{'.cn' if region.startswith('cn-') else ''}/"


def post(url, headers, body, timeout=30):
    """POST and return (status, body bytes). Only AWS's own error text is ever reported, never the request."""
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(20_000_000)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1_000_000)
    except Exception as error:  # the text of a urllib error can quote the request
        raise AwsHttpError("", f"could not reach AWS ({type(error).__name__})") from None


def _text(element, name):
    child = element.find(name)
    return (child.text or "").strip() if child is not None and child.text else ""


def _strip_namespaces(root):
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def _parse_time(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class NativeEc2Client:
    """`describe_instances` and `get_paginator("describe_spot_price_history")` like boto3's EC2 client."""

    def __init__(self, region, credentials, http=post, now=time.time):
        self.region, self._credentials, self._http, self._now = region, credentials, http, now

    def _call(self, action, params):
        credentials = self._credentials.get()
        body = urllib.parse.urlencode(sorted({"Action": action, "Version": API_VERSION, **params}.items())).encode()
        stamp = datetime.fromtimestamp(self._now(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        url = endpoint(self.region)
        headers = sign("POST", url, {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}, body,
                       credentials.access_key, credentials.secret_key, self.region, "ec2", stamp, credentials.token or None)
        status, answer = self._http(url, headers, body)
        try:
            root = _strip_namespaces(ET.fromstring(answer))
        except ET.ParseError:
            raise AwsHttpError("", f"AWS answered something unreadable (HTTP {status})") from None
        if status >= 400:
            error = root.find(".//Error")
            raise AwsHttpError(_text(error, "Code") if error is not None else "", (
                _text(error, "Message") if error is not None else "") or f"HTTP {status}")
        return root

    def describe_instances(self, InstanceIds):
        root = self._call("DescribeInstances", {f"InstanceId.{n}": i for n, i in enumerate(InstanceIds, 1)})
        instances = []
        for item in root.findall("./reservationSet/item/instancesSet/item"):
            instances.append({
                "InstanceId": _text(item, "instanceId"), "ImageId": _text(item, "imageId"),
                "LaunchTime": _parse_time(_text(item, "launchTime")),
                "InstanceLifecycle": _text(item, "instanceLifecycle") or None,
                "Placement": {"AvailabilityZone": _text(item, "placement/availabilityZone")}})
        return {"Reservations": [{"Instances": instances}]}

    def get_paginator(self, name):
        if name != "describe_spot_price_history":
            raise ValueError(f"not supported without boto3: {name}")
        return _SpotPriceHistory(self)


class _SpotPriceHistory:
    def __init__(self, client):
        self._client = client

    def paginate(self, InstanceTypes, ProductDescriptions, StartTime):
        params = {"StartTime": StartTime.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "MaxResults": "1000"}
        params.update({f"InstanceType.{n}": t for n, t in enumerate(InstanceTypes, 1)})
        params.update({f"ProductDescription.{n}": p for n, p in enumerate(ProductDescriptions, 1)})
        token = ""
        while True:
            root = self._client._call("DescribeSpotPriceHistory", {**params, **({"NextToken": token} if token else {})})
            yield {"SpotPriceHistory": [{
                "InstanceType": _text(item, "instanceType"), "AvailabilityZone": _text(item, "availabilityZone"),
                "SpotPrice": _text(item, "spotPrice"), "Timestamp": _parse_time(_text(item, "timestamp"))}
                for item in root.findall("./spotPriceHistorySet/item")]}
            token = _text(root, "nextToken")
            if not token:
                return
