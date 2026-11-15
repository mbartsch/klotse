"""The command that signs a user in through a kubeconfig's exec helper, to run in a terminal (pure: builds argv, runs nothing).

Klotse runs the helper without a terminal, so a browser sign-in or an MFA prompt fails (connect_help explains why). For AWS the
sign-in is `aws sso login` (with the profile the helper was given, if any), or `aws sts get-caller-identity` for a profile
with an MFA device, so the code is typed in the terminal. Any other helper is run with its own arguments in the
terminal, so it can ask its questions there; the user then connects again."""
from .aws_config import sign_in_argv as aws_sign_in_argv
from .connect_help import _helper_name


def _profile(args):
    for index, arg in enumerate(args):
        if arg == "--profile" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return None


def sign_in_argv(info):
    """argv for a terminal, or None when the kubeconfig names no exec helper. `info` is connect_help.auth_info(kubeconfig, context)."""
    command = str(info.get("exec") or "")
    if not command:
        return None
    args = [str(a) for a in info.get("args") or []]
    if _helper_name(command) == "aws":
        return [command] + list(aws_sign_in_argv(_profile(args) or "")[1:])  # keeps the helper's own path
    return [command] + args
