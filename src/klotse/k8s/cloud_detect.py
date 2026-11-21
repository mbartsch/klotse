"""Which cloud a cluster runs in, from what its kubeconfig entry says (#818). No Qt, no network, no files: callers pass
`connect_help.auth_info` (the sign-in helper and the server address). The answer only chooses which cloud's settings Klotse
offers first; the cloud plugins still check each node's providerID unless the user set the cluster's cloud."""
import os
from urllib.parse import urlsplit

CLOUDS = {"aws": "AWS", "azure": "Azure", "gcp": "Google Cloud", "oci": "Oracle Cloud"}  # id -> name shown to the user
AUTO = "auto"

# (host suffix, cloud): managed control planes answer on names under the provider's domain
SERVER_SUFFIXES = ((".eks.amazonaws.com", "aws"), (".eks.amazonaws.com.cn", "aws"), (".azmk8s.io", "azure"),
                   (".oraclecloud.com", "oci"))


def _program(command):
    """The helper's program name without folder and Windows suffix, lower case: C:\\x\\aws.exe -> aws."""
    name = os.path.basename(str(command or "").replace("\\", "/")).lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def from_helper(command, args=()):
    """The cloud a kubeconfig sign-in helper belongs to, or ""."""
    program, args = _program(command), [str(a).lower() for a in args or ()]
    if program in ("aws-iam-authenticator",) or (program == "aws" and "eks" in args and "get-token" in args):
        return "aws"
    if program in ("gke-gcloud-auth-plugin", "gcloud"):
        return "gcp"
    if program in ("kubelogin", "az"):
        return "azure"
    if program == "oci" and "ce" in args:
        return "oci"
    return ""


def from_server(server):
    """The cloud whose managed control plane answers at this API server address, or ""."""
    try:
        host = (urlsplit(str(server or "")).hostname or "").lower()
    except ValueError:
        return ""
    return next((cloud for suffix, cloud in SERVER_SUFFIXES if host.endswith(suffix)), "")


def detect(info):
    """(cloud id, reason) from `auth_info` output; ("", "") when the kubeconfig does not say."""
    info = info or {}
    cloud = from_helper(info.get("exec"), info.get("args"))
    if cloud:
        return cloud, f"from the kubeconfig's sign-in helper ({_program(info.get('exec'))})"
    cloud = from_server(info.get("server"))
    if cloud:
        return cloud, "from the API server address"
    return "", ""


def effective(setting, detected):
    """The cloud whose settings apply: the user's choice, else the detected one, else ""."""
    setting = str(setting or AUTO).strip().lower()
    return setting if setting in CLOUDS else (detected or "")
