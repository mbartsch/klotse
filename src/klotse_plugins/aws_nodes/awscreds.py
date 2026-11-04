"""AWS credentials without boto3, from what an EKS user almost always has: the AWS CLI (the kubeconfig of an EKS cluster runs
`aws eks get-token`) or the usual environment variables.

`aws configure export-credentials --profile NAME --format process` (AWS CLI v2.9 and later) prints the credentials the CLI
itself would use for a profile as JSON: static keys, IAM Identity Center (SSO), an assumed role, `credential_process`. So
every kind of profile works without Klotse knowing how it is set up; if the login has expired the CLI says so (run
`aws sso login`).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

REFRESH_BEFORE_S = 300  # credentials are fetched again this long before they expire
DEFAULT_LIFETIME_S = 900  # for credentials that say nothing about their expiry (static keys never expire)


class CredentialsError(Exception):
    """No usable credentials; the message says what to do."""


@dataclass(frozen=True)
class AwsCredentials:
    access_key: str
    secret_key: str
    token: str = ""
    expires: float = 0.0  # epoch seconds; 0 = does not expire


def _from_environment(environ):
    key, secret = environ.get("AWS_ACCESS_KEY_ID"), environ.get("AWS_SECRET_ACCESS_KEY")
    if key and secret:
        return AwsCredentials(key, secret, environ.get("AWS_SESSION_TOKEN", ""))
    return None


def _parse_expiry(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _run_cli(profile, run, which, environ):
    exe = which("aws")
    if exe is None:
        raise CredentialsError("no AWS credentials: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or install the AWS CLI "
                               "and sign in with it (aws configure, or aws sso login)")
    command = [exe, "configure", "export-credentials", "--format", "process"] + (["--profile", profile] if profile else [])
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window flashing up
    try:
        done = run(command, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=dict(environ), **extra)
    except (OSError, subprocess.SubprocessError) as error:
        raise CredentialsError(f"could not run the AWS CLI ({type(error).__name__})") from None
    if done.returncode != 0:
        lines = (done.stderr or "").strip().splitlines()
        reason = lines[-1][:200] if lines else f"exit status {done.returncode}"
        where = f" for the profile {profile}" if profile else ""
        raise CredentialsError(f"the AWS CLI has no credentials{where}: {reason}")
    try:
        data = json.loads(done.stdout)
        return AwsCredentials(data["AccessKeyId"], data["SecretAccessKey"], data.get("SessionToken", ""),
                              _parse_expiry(data["Expiration"]) if data.get("Expiration") else 0.0)
    except (ValueError, KeyError, TypeError):
        raise CredentialsError("the AWS CLI answered with something unexpected (is it version 2.9 or newer?)") from None


class CredentialSource:
    """Credentials for one AWS profile, fetched when needed and reused until shortly before they expire.

    With a profile name the CLI is asked for that profile. Without one the environment variables are used when they are
    set, otherwise the CLI's default credentials."""

    def __init__(self, profile="", run=subprocess.run, which=shutil.which, environ=None, now=time.time):
        self.profile = profile or ""
        self._run, self._which, self._environ, self._now = run, which, os.environ if environ is None else environ, now
        self._cached, self._lock = None, threading.Lock()

    def get(self):
        with self._lock:
            now = self._now()
            if self._cached is not None and (self._cached.expires == 0 or now < self._cached.expires - REFRESH_BEFORE_S):
                return self._cached
            found = None if self.profile else _from_environment(self._environ)
            if found is None:
                found = _run_cli(self.profile, self._run, self._which, self._environ)
            self._cached = found
            return found
