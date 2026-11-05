"""The optional part that talks to AWS itself (needs boto3 and credentials): the real launch time and AMI of every
instance, and current spot prices. Everything here may fail; the plugin keeps going with what the cluster and the
public price list gave it."""
from datetime import datetime, timezone

CHUNK = 100  # instance ids per DescribeInstances call


class Ec2Unavailable(Exception):
    """boto3 is not installed (an optional extra, so this is not worth a warning)."""


class Ec2Error(Exception):
    """AWS refused or could not be reached; the message is fit to show the user."""


def boto3_session(profile=""):
    try:
        import boto3
    except ImportError as error:
        raise Ec2Unavailable("boto3 is not installed") from error
    try:
        return boto3.Session(profile_name=profile or None)
    except Exception as error:  # an unknown profile name, for example
        raise Ec2Error(str(error)) from error


def _clean(error):
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:200]


class Ec2:
    """DescribeInstances and DescribeSpotPriceHistory per region. `client_factory(region)` makes a boto3 EC2 client."""

    def __init__(self, client_factory):
        self._client_for = client_factory
        self._clients = {}

    def client(self, region):
        if region not in self._clients:
            self._clients[region] = self._client_for(region)
        return self._clients[region]

    def instance_details(self, region, instance_ids):
        """{instance id: {"ami", "launched" (epoch seconds), "spot" (bool), "zone"}} for those that still exist."""
        client, details = self.client(region), {}
        ids = sorted(set(instance_ids))
        for start in range(0, len(ids), CHUNK):
            self._describe(client, ids[start:start + CHUNK], details)
        return details

    def _describe(self, client, chunk, details):
        try:
            pages = client.describe_instances(InstanceIds=chunk)
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
            if code.startswith("InvalidInstanceID") and len(chunk) > 1:
                for one in chunk:  # one gone instance spoils the whole call: ask one by one
                    self._describe(client, [one], details)
                return
            if code.startswith("InvalidInstanceID"):
                return  # that instance no longer exists
            raise Ec2Error(_clean(error)) from error
        for reservation in pages.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                launched = instance.get("LaunchTime")
                details[instance["InstanceId"]] = {
                    "ami": instance.get("ImageId") or "",
                    "launched": launched.timestamp() if isinstance(launched, datetime) else None,
                    "spot": instance.get("InstanceLifecycle") == "spot",
                    "zone": (instance.get("Placement") or {}).get("AvailabilityZone", "")}

    def spot_prices(self, region, instance_types, product="Linux/UNIX"):
        """{(instance type, zone): USD per hour}: the latest price AWS reports for each."""
        types = sorted({t for t in instance_types if t})
        if not types:
            return {}
        latest = {}
        try:
            paginator = self.client(region).get_paginator("describe_spot_price_history")
            for page in paginator.paginate(InstanceTypes=types, ProductDescriptions=[product],
                                           StartTime=datetime.now(timezone.utc)):
                for entry in page.get("SpotPriceHistory", []):
                    key = (entry["InstanceType"], entry["AvailabilityZone"])
                    stamp = entry.get("Timestamp")
                    if key not in latest or (stamp and stamp > latest[key][0]):
                        latest[key] = (stamp, float(entry["SpotPrice"]))
        except Ec2Error:
            raise
        except Exception as error:
            raise Ec2Error(_clean(error)) from error
        return {key: price for key, (_, price) in latest.items()}


def default_ec2(profile=""):
    """An `Ec2` for a profile: through boto3 when it is installed, otherwise through the built-in client (native.py) with the
    credentials the AWS CLI or the environment provide (awscreds.py). Raises Ec2Error when there are no usable credentials."""
    from .awscreds import CredentialSource, CredentialsError
    from .native import NativeEc2Client
    try:
        session = boto3_session(profile)
    except Ec2Unavailable:
        source = CredentialSource(profile)
        try:
            source.get()  # fail once, here, instead of once per region
        except CredentialsError as error:
            raise Ec2Error(str(error)) from None
        return Ec2(lambda region: NativeEc2Client(region, source))
    return Ec2(lambda region: session.client("ec2", region_name=region))
