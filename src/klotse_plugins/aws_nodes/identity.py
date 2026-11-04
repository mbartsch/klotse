"""Which AWS account a profile signs in to: STS GetCallerIdentity (what `aws sts get-caller-identity` asks).

Through boto3 when it is installed, otherwise through the same hand-signed query call the EC2 client uses, with the
credentials the AWS CLI or the environment provide. The answer is only an account id (no secret), kept per profile for
the life of the process: a profile does not change account.
"""
import time
import urllib.parse
from datetime import datetime, timezone

from .awscreds import CredentialSource, CredentialsError
from .ec2 import Ec2Error, Ec2Unavailable, boto3_session
from .native import AwsHttpError, _strip_namespaces, _text, post
from .safe import ParseError, parse_xml, valid_region
from .sigv4 import sign

STS_VERSION = "2011-06-15"


def _endpoint(region):
    if not valid_region(region):
        raise Ec2Error("not a valid AWS region name")
    return f"https://sts.{region}.amazonaws.com{'.cn' if region.startswith('cn-') else ''}/"


def native_account(source, region, http=post, now=time.time):
    """The account id for `source` (a CredentialSource), via a signed GetCallerIdentity call; raises Ec2Error."""
    try:
        credentials = source.get()
    except CredentialsError as error:
        raise Ec2Error(str(error)) from None
    body = urllib.parse.urlencode(sorted({"Action": "GetCallerIdentity", "Version": STS_VERSION}.items())).encode()
    stamp = datetime.fromtimestamp(now(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    url = _endpoint(region)
    headers = sign("POST", url, {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}, body,
                   credentials.access_key, credentials.secret_key, region, "sts", stamp, credentials.token or None)
    try:
        status, answer = http(url, headers, body)
        root = _strip_namespaces(parse_xml(answer))
    except AwsHttpError as error:
        raise Ec2Error(str(error)) from None
    except ParseError:
        raise Ec2Error("AWS answered something unreadable to GetCallerIdentity") from None
    if status >= 400:
        error = root.find(".//Error")
        raise Ec2Error(f"GetCallerIdentity: {_text(error, 'Message') if error is not None else ''}".strip(": ") or f"HTTP {status}")
    account = _text(root, "GetCallerIdentityResult/Account")
    if not account:
        raise Ec2Error("GetCallerIdentity did not say the account")
    return account


def caller_account(profile, region):
    """The account id the profile ("" = default credentials) belongs to; raises Ec2Error with a message to show."""
    try:
        session = boto3_session(profile)
    except Ec2Unavailable:
        return native_account(CredentialSource(profile), region)
    try:
        return str(session.client("sts", region_name=region).get_caller_identity()["Account"])
    except Exception as error:  # an expired login, no network, ...
        text = str(error).strip().splitlines()
        raise Ec2Error(text[0][:200] if text else type(error).__name__) from None
