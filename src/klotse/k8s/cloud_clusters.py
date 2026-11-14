"""Clusters from a cloud account, added to a kubeconfig file (no Qt).

Each provider's own CLI lists the clusters and writes a cluster's credentials. Every CLI call writes into a temporary file that
Klotse creates; the merge then copies the new entries into the target file (the generated `klotse-cloud.klotse`, or the main
kubeconfig when the user chose that in Preferences). The main kubeconfig is never a CLI target. Arguments are passed as argv
lists; nothing goes through a shell, and values from a cloud (names, ids) are checked before use.
"""
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

GENERATED_FILE = "klotse-cloud.klotse"  # the file Klotse writes when the main kubeconfig is not chosen
SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@()-]{0,199}")  # cluster names, ids, groups, profiles, projects
AWS_REGION = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}")
OCI_REGION = re.compile(r"[a-z]{2,4}(?:-[a-z]+)+-\d")
CLI_TIMEOUT_S = 120


class CloudError(Exception):
    """A provider CLI is missing, failed, or answered with something Klotse cannot use. The message is safe to show."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    required: bool = False


@dataclass(frozen=True)
class Provider:
    key: str
    title: str
    cli: str
    fields: tuple


PROVIDERS = {
    "aws": Provider("aws", "AWS (EKS)", "aws", (Field("region", "Region", True), Field("profile", "AWS profile"))),
    "gcp": Provider("gcp", "Google Cloud (GKE)", "gcloud", (Field("project", "Project ID", True), Field("location", "Location (zone or region; optional, looked up when empty)"))),
    "azure": Provider("azure", "Azure (AKS)", "az", (Field("subscription", "Subscription (id or name)"), Field("resource_group", "Resource group"))),
    "oci": Provider("oci", "Oracle Cloud (OKE)", "oci", (Field("compartment", "Compartment OCID", True), Field("region", "Region", True), Field("profile", "OCI config profile"))),
}


@dataclass(frozen=True)
class ClusterRef:
    name: str
    ref: str  # the value the provider CLI needs to name the cluster (the name, or the OCID for OCI)
    location: str = ""  # region, zone or location, for display and for the CLI
    group: str = ""  # resource group (Azure)
    state: str = ""


def _checked(value, pattern, what):
    value = str(value or "").strip()
    if not value:
        return ""
    if not pattern.fullmatch(value):
        raise CloudError(f"{what} has characters that are not allowed: {value[:40]!r}")
    return value


def clean_scope(provider, scope):
    """The scope values of a provider, checked and stripped. Missing required values raise CloudError."""
    spec = PROVIDERS[provider]
    out = {}
    for field in spec.fields:
        pattern = AWS_REGION if (provider == "aws" and field.key == "region") else \
            OCI_REGION if (provider == "oci" and field.key == "region") else SAFE_VALUE
        out[field.key] = _checked(scope.get(field.key), pattern, field.label)
        if field.required and not out[field.key]:
            raise CloudError(f"{field.label} is required for {spec.title}")
    return out


def _ini(path):
    """The parsed INI file, or None when it is missing or unreadable."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeDecodeError, configparser.Error):
        return None
    return parser


def _env_value(env, *names):
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return ""


def _aws_defaults(env):
    """Profile and region from AWS_PROFILE / AWS_DEFAULT_REGION, else the profile's `region` in the AWS config file."""
    profile = _env_value(env, "AWS_PROFILE", "AWS_DEFAULT_PROFILE")
    found = {"profile": profile} if profile else {}
    region = _env_value(env, "AWS_DEFAULT_REGION", "AWS_REGION")
    if not region:
        parser = _ini(os.path.expanduser(env.get("AWS_CONFIG_FILE") or "~/.aws/config"))
        section = f"profile {profile}" if profile and profile != "default" else "default"
        if parser is not None and parser.has_section(section):
            region = parser[section].get("region", "").strip()
    if region:
        found["region"] = region
    return found


def _gcp_defaults(env):
    """The project of the active gcloud configuration. Location is left empty: an empty location lists every cluster."""
    config_dir = os.path.expanduser(env.get("CLOUDSDK_CONFIG") or "~/.config/gcloud")
    name = _env_value(env, "CLOUDSDK_ACTIVE_CONFIG_NAME")
    if not name:
        try:
            with open(os.path.join(config_dir, "active_config"), encoding="utf-8") as handle:
                name = handle.read().strip()
        except OSError:
            name = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        name = "default"
    project = _env_value(env, "CLOUDSDK_CORE_PROJECT")
    if not project:
        parser = _ini(os.path.join(config_dir, "configurations", f"config_{name}"))
        if parser is not None and parser.has_section("core"):
            project = parser["core"].get("project", "").strip()
    return {"project": project} if project else {}


def _azure_defaults(env):
    """The default subscription of the Azure CLI (the one `az account set` chose). The resource group is left empty:
    an empty group lists every cluster in the subscription."""
    config_dir = os.path.expanduser(env.get("AZURE_CONFIG_DIR") or "~/.azure")
    with open(os.path.join(config_dir, "azureProfile.json"), encoding="utf-8-sig") as handle:
        data = json.load(handle)
    for item in (data.get("subscriptions") or []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("isDefault") and item.get("id"):
            return {"subscription": str(item["id"])}
    return {}


def detect_defaults(provider, env=None):
    """The scope values a provider's CLI would use by default, from the environment and the CLI's local config files, so the
    dialog can offer them. Missing or unreadable files give {}. The user can change every value."""
    env = os.environ if env is None else env
    detectors = {"aws": _aws_defaults, "gcp": _gcp_defaults, "azure": _azure_defaults, "oci": oci_defaults}
    detector = detectors.get(provider)
    if detector is None:
        return {}
    try:
        return detector(env)
    except (OSError, ValueError, configparser.Error):
        return {}


def oci_defaults(env=None):
    """Region, compartment and profile for OCI, from the environment and the OCI config file the CLI reads: OCI_CLI_CONFIG_FILE
    (default ~/.oci/config) and OCI_CLI_PROFILE (default DEFAULT). The region is the profile's `region`; the compartment
    defaults to its `tenancy` (the root compartment). Nothing found, or an unreadable file, gives {}. The dialog shows these
    values, which the user can change. The endpoint is not read here: the OCI CLI itself honours OCI_CLI_ENDPOINT."""
    env = os.environ if env is None else env
    path = os.path.expanduser(env.get("OCI_CLI_CONFIG_FILE") or "~/.oci/config")
    profile = (env.get("OCI_CLI_PROFILE") or "DEFAULT").strip()
    parser = _ini(path)
    if parser is None:
        return {}
    if profile == "DEFAULT":
        values = dict(parser.defaults())
    elif parser.has_section(profile):
        values = dict(parser[profile])
    else:
        return {}
    found = {}
    if values.get("region", "").strip():
        found["region"] = values["region"].strip()
    if values.get("tenancy", "").strip():
        found["compartment"] = values["tenancy"].strip()
    if env.get("OCI_CLI_PROFILE", "").strip():
        found["profile"] = profile
    return found


def list_argv(provider, scope):
    """argv that lists the clusters of the scope, as JSON."""
    s = clean_scope(provider, scope)
    if provider == "aws":
        return ["aws", "eks", "list-clusters", "--region", s["region"], "--output", "json"] + (["--profile", s["profile"]] if s["profile"] else [])
    if provider == "gcp":
        return ["gcloud", "container", "clusters", "list", "--project", s["project"], "--format", "json"] + (["--location", s["location"]] if s["location"] else [])
    if provider == "azure":
        return ["az", "aks", "list", "--output", "json"] + (["--resource-group", s["resource_group"]] if s["resource_group"] else []) + (
            ["--subscription", s["subscription"]] if s["subscription"] else [])
    return ["oci", "ce", "cluster", "list", "--compartment-id", s["compartment"], "--region", s["region"], "--all"] + (
        ["--profile", s["profile"]] if s["profile"] else [])


def parse_clusters(provider, text, scope=None):
    """The clusters in a provider's list answer. Entries whose names are not usable are left out (not an error)."""
    try:
        data = json.loads(text or "null")
    except ValueError:
        raise CloudError("the provider CLI answered with something that is not JSON") from None
    if provider == "oci":
        data = (data or {}).get("data") if isinstance(data, dict) else None
    elif provider == "aws":
        data = (data or {}).get("clusters") if isinstance(data, dict) else None
    if not isinstance(data, list):
        raise CloudError("the provider CLI answered with an unexpected list")
    region = clean_scope(provider, scope or {}).get("region", "") if provider in ("aws", "oci") else ""
    clusters = []
    for item in data:
        if provider == "aws":
            name = str(item) if isinstance(item, str) else ""
            found = ClusterRef(name, name, region) if SAFE_VALUE.fullmatch(name) else None
        else:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not SAFE_VALUE.fullmatch(name):
                continue
            if provider == "gcp":
                location = str(item.get("location") or item.get("zone") or item.get("region") or "")
                found = ClusterRef(name, name, location, state=str(item.get("status") or ""))
            elif provider == "azure":
                group = str(item.get("resourceGroup") or "")
                found = ClusterRef(name, name, str(item.get("location") or ""), group, str(item.get("provisioningState") or ""))
            else:
                ocid = str(item.get("id") or "")
                found = ClusterRef(name, ocid, region, state=str(item.get("lifecycle-state") or "")) if ocid.startswith("ocid1.") else None
        if found is not None and all(c.ref != found.ref for c in clusters):
            clusters.append(found)
    return clusters


def credentials_argv(provider, cluster, scope, target):
    """(argv, extra environment) that writes the credentials of `cluster` into the file `target`."""
    s = clean_scope(provider, scope)
    target = str(target)
    if provider == "aws":
        argv = ["aws", "eks", "update-kubeconfig", "--name", cluster.name, "--region", s["region"], "--kubeconfig", target]
        return argv + (["--profile", s["profile"]] if s["profile"] else []), {}
    if provider == "gcp":
        if not cluster.location:
            raise CloudError(f"the location of {cluster.name} is not known: see resolve_location")
        argv = ["gcloud", "container", "clusters", "get-credentials", cluster.name, "--project", s["project"], "--location", cluster.location]
        return argv, {"KUBECONFIG": target}
    if provider == "azure":
        argv = ["az", "aks", "get-credentials", "--name", cluster.name, "--file", target]
        argv += ["--resource-group", cluster.group] if cluster.group else []
        return argv + (["--subscription", s["subscription"]] if s["subscription"] else []), {}
    argv = ["oci", "ce", "cluster", "create-kubeconfig", "--cluster-id", cluster.ref, "--file", target, "--region", s["region"], "--token-version", "2.0.0"]
    return argv + (["--profile", s["profile"]] if s["profile"] else []), {}


def run(argv, env=None, timeout=CLI_TIMEOUT_S):
    """Run one provider CLI and return its standard output. Errors are CloudError with a short reason, never the full output."""
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window
    environ = dict(os.environ, **(env or {}))
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=environ, **extra)  # nosec B603 - argv list from this module, no shell
    except FileNotFoundError:
        raise CloudError(f"the {argv[0]} command is not installed or not on the PATH") from None
    except subprocess.TimeoutExpired:
        raise CloudError(f"{argv[0]} did not answer within {timeout} seconds") from None
    except OSError as error:
        raise CloudError(f"could not run {argv[0]} ({type(error).__name__})") from None
    if done.returncode != 0:
        raise CloudError(f"{argv[0]} failed: {failure_reason(done.stderr or '', done.returncode)}")
    return done.stdout


def failure_reason(stderr, returncode):
    """A short reason from a failed CLI's stderr. OCI ends its error with a JSON block, so the last line is only "}": the
    "code" and "message" inside that block are used when present."""
    fields = re.findall(r'^\s*"(code|message)":\s*"([^"]*)"', stderr, re.MULTILINE)
    if fields:
        return ": ".join(value for _, value in fields[:2])[:240]
    lines = stderr.strip().splitlines()
    return lines[-1][:240] if lines else f"exit status {returncode}"


def resolve_location(provider, cluster, scope):
    """The cluster with its location filled in. GKE needs --location (a zone or a region) for get-credentials. The list answer
    usually gives it; when it does not, the Location field is used, else a filtered list gives it (one more gcloud call)."""
    if provider != "gcp" or cluster.location:
        return cluster
    s = clean_scope(provider, scope)
    if s["location"]:
        return replace(cluster, location=s["location"])
    text = run(["gcloud", "container", "clusters", "list", "--project", s["project"], "--filter", f"name={cluster.name}",
                "--format", "value(location)"])
    found = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not found or not SAFE_VALUE.fullmatch(found):
        raise CloudError(f"could not find the location of {cluster.name}: enter its zone or region in Location and try again")
    return replace(cluster, location=found)


def default_generated_path(home=None):
    """~/.kube/klotse-cloud.klotse: where clusters go unless the main kubeconfig was chosen."""
    return Path(home or Path.home()) / ".kube" / GENERATED_FILE


def main_kubeconfig_path(environ=None, home=None):
    """The main kubeconfig: the first KUBECONFIG entry, else ~/.kube/config."""
    environ = os.environ if environ is None else environ
    first = next((p for p in environ.get("KUBECONFIG", "").split(os.pathsep) if p), "")
    return Path(first) if first else Path(home or Path.home()) / ".kube" / "config"


def _load(path):
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise CloudError(f"{path} is not a readable kubeconfig") from None
    if data is not None and not isinstance(data, dict):
        raise CloudError(f"{path} is not a kubeconfig")
    return data or {}


def merge_into(target, source, backup=True):
    """Copy the clusters, contexts and users of the kubeconfig `source` into `target` (created when missing).

    An entry whose name the target already has is not copied (reported in `skipped`). When the target exists it is first copied
    to `<target>.bak`. The file is written through a temporary file and replaced in one step. Returns (added contexts, skipped)."""
    target, source = Path(target), Path(source)
    new = _load(source) or {}
    current = _load(target)
    existing = current is not None
    if current is None:
        current = {}
    if backup and existing:
        shutil.copy2(target, Path(f"{target}.bak"))
    merged = {"apiVersion": current.get("apiVersion") or new.get("apiVersion") or "v1",
              "kind": current.get("kind") or "Config", "preferences": current.get("preferences") or {}}
    added, skipped = [], []
    for section in ("clusters", "users", "contexts"):
        have = [item for item in (current.get(section) or []) if isinstance(item, dict)]
        names = {item.get("name") for item in have}
        for item in (new.get(section) or []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if item["name"] in names:
                if section == "contexts":
                    skipped.append(item["name"])
                continue
            have.append(item)
            names.add(item["name"])
            if section == "contexts":
                added.append(item["name"])
        merged[section] = have
    merged["current-context"] = current.get("current-context") or (added[0] if added else "")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if os.name == "posix":
        os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return added, skipped


def scratch_dir():
    """A new private folder for the CLI's temporary kubeconfig; the caller removes it."""
    return Path(tempfile.mkdtemp(prefix="klotse-cloud-"))
