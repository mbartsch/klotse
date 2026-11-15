"""MFA sign-in for AWS profiles: detection from the config file, the sign-in command, and the message when the code is
missing (no network, no AWS CLI needed)."""
import subprocess

import pytest

from klotse_plugins.aws.awscreds import CredentialSource, CredentialsError, mfa_serial, sign_in_argv, sso_login_argv


CONFIG = """
[default]
region = eu-west-1

[profile sso-only]
sso_start_url = https://example.awsapps.com/start

[profile role-with-mfa]
role_arn = arn:aws:iam::123456789012:role/Ops
source_profile = base
mfa_serial = arn:aws:iam::123456789012:mfa/marcelo

[profile broken]
mfa_serial = [unterminated
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config"
    path.write_text(CONFIG, encoding="utf-8")
    return {"AWS_CONFIG_FILE": str(path)}


def test_mfa_serial_is_read_from_the_named_profile(config):
    assert mfa_serial("role-with-mfa", config) == "arn:aws:iam::123456789012:mfa/marcelo"


def test_profile_without_an_mfa_device_has_none(config):
    assert mfa_serial("sso-only", config) == ""
    assert mfa_serial("missing", config) == ""


def test_default_profile_and_aws_profile_variable_are_used_when_no_name_is_given(config):
    assert mfa_serial("", config) == ""  # [default] has no MFA device
    assert mfa_serial("", {**config, "AWS_PROFILE": "role-with-mfa"}) == "arn:aws:iam::123456789012:mfa/marcelo"


def test_unreadable_or_broken_config_means_no_mfa(tmp_path):
    assert mfa_serial("role-with-mfa", {"AWS_CONFIG_FILE": str(tmp_path / "absent")}) == ""
    bad = tmp_path / "bad"
    bad.write_text("[profile broken\n", encoding="utf-8")
    assert mfa_serial("broken", {"AWS_CONFIG_FILE": str(bad)}) == ""


def test_sign_in_for_an_mfa_profile_caches_its_role_credentials(config):
    assert sign_in_argv("role-with-mfa", config) == ("aws", "sts", "get-caller-identity", "--profile", "role-with-mfa")


def test_sign_in_for_an_sso_profile_is_sso_login(config):
    assert sign_in_argv("sso-only", config) == sso_login_argv("sso-only") == ("aws", "sso", "login", "--profile", "sso-only")
    assert sign_in_argv("", config) == ("aws", "sso", "login")


class Done:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_missing_code_for_an_mfa_profile_says_to_sign_in(config):
    run = lambda *a, **k: Done(255, stderr="Enter MFA code for arn:aws:iam::123456789012:mfa/marcelo: \nError when retrieving")
    source = CredentialSource("role-with-mfa", run=run, which=lambda name: "/usr/bin/aws", environ=config, now=lambda: 0.0)
    with pytest.raises(CredentialsError, match="needs an MFA code.*Sign in to AWS"):
        source.get()


def test_other_failures_keep_the_cli_reason(config):
    run = lambda *a, **k: Done(255, stderr="The SSO session has expired")
    source = CredentialSource("sso-only", run=run, which=lambda name: "/usr/bin/aws", environ=config, now=lambda: 0.0)
    with pytest.raises(CredentialsError, match="SSO session has expired") as error:
        source.get()
    assert "MFA" not in str(error.value)


def test_the_cli_is_still_run_without_stdin(config):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)
        return Done(0, stdout='{"AccessKeyId": "AKIA", "SecretAccessKey": "s"}')

    CredentialSource("sso-only", run=run, which=lambda name: "/usr/bin/aws", environ=config, now=lambda: 0.0).get()
    assert seen["stdin"] == subprocess.DEVNULL


def test_exec_helper_sign_in_uses_the_mfa_command_for_an_mfa_profile(config, monkeypatch):
    from klotse.k8s import aws_config
    from klotse.k8s.sign_in import sign_in_argv as helper_sign_in

    monkeypatch.setenv("AWS_CONFIG_FILE", config["AWS_CONFIG_FILE"])
    info = {"exec": "aws", "args": ["eks", "get-token", "--cluster-name", "c", "--profile", "role-with-mfa"]}
    assert helper_sign_in(info) == ["aws", "sts", "get-caller-identity", "--profile", "role-with-mfa"]
    info["args"] = ["eks", "get-token", "--profile", "sso-only"]
    assert helper_sign_in(info) == ["aws", "sso", "login", "--profile", "sso-only"]
    assert aws_config.mfa_serial("role-with-mfa") == "arn:aws:iam::123456789012:mfa/marcelo"
