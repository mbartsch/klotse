"""Run the local Floci lab with one command.

    python3 lab.py up [--providers aws,gcp] [--identities]   start the emulators and apply each Terraform root
    python3 lab.py down [--providers ...]                     remove lab identities, destroy the roots, stop the emulators
    python3 lab.py env [--shell sh|powershell]                print the variables for a shell (eval "$(python3 lab.py env)")

Everything the lab generates (CA bundle, throwaway OCI key and profile, gcloud and az config folders) stays in .local/.
The commands only talk to the emulators on 127.0.0.1; see README.md for the isolation the lab expects.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

import start

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
ORDER = ("aws", "azure", "gcp", "oci")  # apply order; destroy runs in reverse
HEALTH = {
    "aws": "http://127.0.0.1:4566/_floci/health",
    "azure": "http://127.0.0.1:4577/_floci/health",  # HTTP also for Azure (README)
    "gcp": "http://127.0.0.1:4588/_floci/health",
    "oci": "http://127.0.0.1:4599/_floci/health",
}
AZURE_CA_URL = "http://127.0.0.1:4577/_floci/tls-cert"
OCI_OVERRIDE = "oci_containerengine.ContainerEngineClient=http://127.0.0.1:4599"
GCP_TOKEN = "floci-local-test-token"  # the same fixture token as gcp/main.tf; Floci accepts any non-empty token
SYSTEM_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")
OCI_PROFILE = """[DEFAULT]
tenancy=ocid1.tenancy.oc1..flocilocaltenancy0000000000000000000000000000000000000000
user=ocid1.user.oc1..flociiacuser0000000000000000000000000000000000000000
fingerprint={fingerprint}
key_file={key}
region=us-ashburn-1
"""


def log(text):
    print(f"[lab] {text}", file=sys.stderr, flush=True)


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def write_private(path, data):
    """Write a file only the current user can read (data: str or bytes)."""
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def paths(local=LOCAL):
    return {
        "azure_ca": local / "floci-az.crt",
        "roots": local / "roots.pem",
        "oci_key": local / "oci.pem",
        "oci_config": local / "oci-config",
        "gcp_token": local / "gcp-token",
        "gcloud": local / "gcloud",
        "azure_config": local / "azure",
    }


def environment(local=LOCAL):
    """The variables Terraform and the cloud CLIs need to reach the emulators (none are real credentials)."""
    p = paths(local)
    env = {}
    if p["roots"].is_file():  # written by `up`; never point a shell at a CA bundle that does not exist
        # Terraform azurerm (Go) and the az CLI (Python requests) trust Floci Azure's generated CA through these.
        env["SSL_CERT_FILE"] = env["REQUESTS_CA_BUNDLE"] = str(p["roots"])
    return env | {
        "AZURE_CONFIG_DIR": str(p["azure_config"]),
        # OCI: Terraform reads both TF_VAR_ values; the OCI CLI reads the last two.
        "TF_VAR_oci_config_file": str(p["oci_config"]),
        "TF_VAR_CLIENT_HOST_OVERRIDES": OCI_OVERRIDE,
        "OCI_CLI_CONFIG_FILE": str(p["oci_config"]),
        "OCI_CLI_ENDPOINT": "http://127.0.0.1:4599",
        "SUPPRESS_LABEL_WARNING": "True",
        # AWS CLI and SDKs.
        "AWS_ENDPOINT_URL": "http://127.0.0.1:4566",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
        # gcloud: a private config folder, the emulator's endpoints (floci-gcp docs/services/gke.md) and a fixture token.
        "CLOUDSDK_CONFIG": str(p["gcloud"]),
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER": "http://localhost:4588/container/",
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_SERVICEUSAGE": "http://localhost:4588/",
        "CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API": "false",
        "CLOUDSDK_CORE_PROJECT": "klotse-local",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE": str(p["gcp_token"]),
    }


def render(env, shell):
    if shell == "powershell":
        return "".join(f"$env:{name} = '{value.replace(chr(39), chr(39) * 2)}'\n" for name, value in env.items())
    return "".join(f"export {name}={shlex.quote(value)}\n" for name, value in env.items())


def wait_healthy(providers, timeout=300, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    waiting = list(providers)
    while waiting:
        for provider in list(waiting):
            try:
                with opener(HEALTH[provider], timeout=5) as answer:
                    if answer.status == 200:
                        waiting.remove(provider)
                        log(f"{provider} emulator is up")
            except (urllib.error.URLError, OSError):
                pass
        if waiting:
            if clock() > deadline:
                raise SystemExit(f"Emulators did not become healthy: {', '.join(waiting)}")
            sleep(3)


def system_bundle():
    return next((Path(candidate) for candidate in SYSTEM_BUNDLES if Path(candidate).is_file()), None)


def oci_fingerprint(key):
    der = subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def prepare(providers, local=LOCAL, fetch=urllib.request.urlopen):
    """Create the lab-only files under .local/ that the chosen providers need. Existing files are kept."""
    p = paths(local)
    private_dir(local)
    private_dir(p["gcloud"])
    private_dir(p["azure_config"])
    if not p["gcp_token"].exists():
        write_private(p["gcp_token"], GCP_TOKEN + "\n")
    if "azure" in providers:
        with fetch(AZURE_CA_URL, timeout=10) as answer:
            ca = answer.read()
        write_private(p["azure_ca"], ca)
        bundle = system_bundle()
        roots = (bundle.read_bytes().rstrip(b"\n") + b"\n" if bundle else b"") + ca
        write_private(p["roots"], roots)
    elif not p["roots"].exists() and system_bundle():
        write_private(p["roots"], system_bundle().read_bytes())
    if "oci" in providers and not p["oci_config"].exists():
        if not p["oci_key"].exists():
            subprocess.run(["openssl", "genrsa", "-out", str(p["oci_key"]), "2048"], check=True, capture_output=True)
            os.chmod(p["oci_key"], 0o600)
        write_private(p["oci_config"], OCI_PROFILE.format(fingerprint=oci_fingerprint(p["oci_key"]),
                                                         key=p["oci_key"].as_posix()))


def terraform(provider, *args, env, capture=False):
    argv = ["terraform", f"-chdir={HERE / provider}", *args]
    return subprocess.run(argv, check=True, env=env, text=True, capture_output=capture)


def lab_env():
    env = os.environ.copy()
    for key in list(env):
        if key.lower().endswith("_proxy") or key == "TF_LOG":
            env.pop(key)  # README: no proxies or Terraform tracing in the lab
    env.update(environment())
    return env


def selected(value):
    names = [name.strip() for name in (value or ",".join(ORDER)).split(",") if name.strip()]
    unknown = [name for name in names if name not in ORDER]
    if unknown:
        raise SystemExit(f"Unknown provider(s): {', '.join(unknown)} (use {', '.join(ORDER)})")
    return [name for name in ORDER if name in names]


def oci_ids(env):
    out = terraform("oci", "output", "-json", "clusters", env=env, capture=True).stdout
    ids = json.loads(out)
    return [ids["klotse-oke-1"], ids["klotse-oke-2"]]


def identity(action, provider, env):
    argv = [sys.executable, str(HERE / "identity.py"), action, provider]
    if provider == "oci":
        argv += ["--oci-ids", *oci_ids(env)]
    subprocess.run(argv, check=True, env=env)


def up(args):
    providers = selected(args.providers)
    start.main()
    wait_healthy(providers, timeout=args.timeout)
    prepare(providers)
    env = lab_env()
    for provider in providers:
        log(f"terraform apply: {provider}")
        try:
            terraform(provider, "init", "-input=false", "-lockfile=readonly", env=env)
            terraform(provider, "plan", "-input=false", "-out=lab.tfplan", env=env)
            terraform(provider, "apply", "-input=false", "lab.tfplan", env=env)
        except subprocess.CalledProcessError:
            if provider == "aws":
                log("AWS failed; if it reported '409 Cluster already exists', follow 'AWS: recover a cluster created before "
                    "a conflict response' in README.md, then run up again with --providers for the rest.")
            raise SystemExit(f"terraform failed for {provider}; the emulators are still running (lab.py down stops them).")
    if args.identities:
        for provider in providers:
            if not (LOCAL / f"{provider}.kubeconfig").exists():
                identity("create", provider, env)
    log("Lab is up. Variables for a shell: eval \"$(python3 lab.py env)\"" +
        ("" if args.identities else "; Klotse identities: python3 identity.py create <provider> (see IDENTITIES.md)"))


def down(args):
    providers = selected(args.providers)
    env = lab_env()
    failed = []
    for provider in reversed(providers):
        try:
            if (LOCAL / f"{provider}.kubeconfig").exists():
                identity("remove", provider, env)
            if (HERE / provider / ".terraform").is_dir():
                log(f"terraform destroy: {provider}")
                terraform(provider, "destroy", "-input=false", "-auto-approve", env=env)
        except subprocess.CalledProcessError:
            failed.append(provider)
    if failed:
        raise SystemExit(f"Destroy failed for {', '.join(failed)}; the emulators were left running so you can retry.")
    if set(providers) == set(ORDER):
        subprocess.run(["docker", "compose", "-f", str(HERE / "compose.yaml"), "down", "--volumes"], check=True)
        log("Emulators stopped. Check README.md 'Tear down' for leftover containers before deleting state.")
    else:
        log("Only some providers were destroyed; the emulators are still running.")


def env_command(args):
    if not paths()["roots"].is_file():
        log("No .local/roots.pem yet (run `lab.py up`): SSL_CERT_FILE and REQUESTS_CA_BUNDLE are left out.")
    sys.stdout.write(render(environment(), args.shell))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    up_parser = commands.add_parser("up", help="start the emulators and apply the Terraform roots")
    up_parser.add_argument("--providers", help="comma-separated subset of aws,azure,gcp,oci (default: all)")
    up_parser.add_argument("--identities", action="store_true", help="also create the Klotse lab kubeconfigs (identity.py)")
    up_parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for the emulators (default 300)")
    down_parser = commands.add_parser("down", help="remove identities, destroy the roots and stop the emulators")
    down_parser.add_argument("--providers", help="comma-separated subset; the emulators stop only when all are destroyed")
    env_parser = commands.add_parser("env", help="print the lab variables for a shell")
    env_parser.add_argument("--shell", choices=("sh", "powershell"), default="sh")
    args = parser.parse_args(argv)
    {"up": up, "down": down, "env": env_command}[args.command](args)


if __name__ == "__main__":
    main()
