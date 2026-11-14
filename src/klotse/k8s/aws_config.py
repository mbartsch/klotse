"""AWS profile facts from the AWS config file, shared by the core sign-in and the AWS plugin. Reads the config only, never
credentials. Pure: builds argv and reads one file."""
import configparser
import os


def mfa_serial(profile, environ=None, home=None):
    """The `mfa_serial` (the MFA device) of a profile in the AWS config file, or "" when it has none or the file cannot be read.
    The profile is `profile`, else AWS_PROFILE, else the default one. Only the config file is read: no credentials."""
    environ = os.environ if environ is None else environ
    name = profile or environ.get("AWS_PROFILE") or "default"
    path = environ.get("AWS_CONFIG_FILE") or os.path.join(home or os.path.expanduser("~"), ".aws", "config")
    parser = configparser.RawConfigParser()
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error, UnicodeDecodeError):
        return ""
    section = "default" if name == "default" else f"profile {name}"
    if not parser.has_section(section):
        return ""
    return parser.get(section, "mfa_serial", fallback="").strip()


def sso_login_argv(profile):
    """argv that signs in with IAM Identity Center for `profile` (empty = the default profile), to run in a terminal."""
    return ("aws", "sso", "login") + (("--profile", profile) if profile else ())


def sign_in_argv(profile, environ=None):
    """argv that signs in for `profile` in a terminal, so that the MFA code can be typed there. A profile with an MFA device
    (assume-role with `mfa_serial`) is signed in with `aws sts get-caller-identity`: the AWS CLI caches the role's credentials
    after the code, and `export-credentials` and `aws eks get-token` then use that cache. Everything else is `aws sso login`."""
    if mfa_serial(profile, environ):
        return ("aws", "sts", "get-caller-identity") + (("--profile", profile) if profile else ())
    return sso_login_argv(profile)
