"""Signing in to the AWS console without a login page, with AWS's documented "custom identity broker" flow.

The steps (https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_enable-console-custom-url.html):

1. get temporary credentials: the profile's own credentials when they are temporary already (IAM Identity Center, an
   assumed role), a role in the cluster's account via STS AssumeRole (several accounts), or an STS GetFederationToken
   when the profile has long-term IAM user keys;
2. send them to AWS's federation endpoint (POST, so they never sit in a URL) and get a one-time SigninToken back;
3. build the login URL with that token and the console page to land on, and open it in the browser.

The login URL is a secret for 15 minutes (anyone who opens it is signed in), so it is only ever handed to the browser:
it is not logged, shown, copied or kept, and error messages never contain the request or the credentials.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .safe import require_https

ENDPOINTS = {False: "https://signin.aws.amazon.com/federation", True: "https://signin.amazonaws-us-gov.com/federation"}
ISSUER = "https://github.com/mbartsch/klotse"  # where AWS sends the user when the console session expires
SESSION_NAME = "klotse-console"  # a role session / federated user name (2-32 characters)
ALLOW_ALL = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]})
# A federation token can never do more than the IAM user can, so allow-all only means "whatever the user may do".


class FederationError(Exception):
    """Signing in automatically did not work; the message is fit to show the user (it never holds a secret)."""


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    token: str


def short(error):
    """The first line of an AWS error, without anything long enough to hold a request."""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:200]


def fetch_json(endpoint, form, timeout=20):
    """POST a form to the federation endpoint and return its JSON answer. Errors say what failed, never what was sent."""
    request = urllib.request.Request(require_https(endpoint), data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"User-Agent": "Klotse-aws-nodes"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - https only, checked above
            return json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as error:
        raise FederationError(f"AWS sign-in answered HTTP {error.code}") from None
    except Exception as error:  # the text of a urllib or json error can quote the request: say only the kind
        raise FederationError(f"could not reach the AWS sign-in service ({type(error).__name__})") from None


def console_credentials(session, account=None, role=None, gov=False):
    """Temporary credentials to sign in with, from a boto3 session (see the module notes for the three sources)."""
    found = session.get_credentials()
    if found is None:
        raise FederationError("no AWS credentials were found for this profile")
    frozen = found.get_frozen_credentials()
    sts = session.client("sts")
    if account and role:  # several accounts: a role in the cluster's account
        arn = f"arn:{'aws-us-gov' if gov else 'aws'}:iam::{account}:role/{role}"
        try:
            got = sts.assume_role(RoleArn=arn, RoleSessionName=SESSION_NAME)["Credentials"]
        except Exception as error:
            raise FederationError(f"could not assume the role {role} in account {account}: {short(error)}") from None
        return Credentials(got["AccessKeyId"], got["SecretAccessKey"], got["SessionToken"])
    if frozen.token:  # IAM Identity Center or an assumed role: temporary already
        return Credentials(frozen.access_key, frozen.secret_key, frozen.token)
    try:  # long-term IAM user keys cannot sign in directly
        got = sts.get_federation_token(Name=SESSION_NAME, Policy=ALLOW_ALL, DurationSeconds=3600)["Credentials"]
    except Exception as error:
        raise FederationError(f"could not get a federation token: {short(error)}") from None
    return Credentials(got["AccessKeyId"], got["SecretAccessKey"], got["SessionToken"])


def login_url(credentials, destination, gov=False, fetch=fetch_json):
    """The one-time URL that signs the browser in and opens `destination`. Treat it as a secret."""
    endpoint = ENDPOINTS[bool(gov)]
    session = json.dumps({"sessionId": credentials.access_key, "sessionKey": credentials.secret_key,
                          "sessionToken": credentials.token})
    answer = fetch(endpoint, {"Action": "getSigninToken", "Session": session})
    token = answer.get("SigninToken") if isinstance(answer, dict) else None
    if not token:
        raise FederationError("AWS sign-in did not return a sign-in token")
    return (f"{endpoint}?Action=login&Issuer={urllib.parse.quote_plus(ISSUER)}"
            f"&Destination={urllib.parse.quote_plus(destination)}&SigninToken={urllib.parse.quote_plus(token)}")
