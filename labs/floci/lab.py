"""Run the local Floci lab with one command.

    python3 lab.py up [--providers aws,gcp] [--identities]   start the emulators and apply each Terraform root
    python3 lab.py down [--providers ...]                     remove lab identities, destroy the roots, stop the emulators
    python3 lab.py down --purge                               no Terraform: remove every Docker object of the lab, then
                                                              its local Terraform state (also after a failed up or down)
    python3 lab.py env [--shell sh|powershell]                print the variables for a shell (eval "$(python3 lab.py env)")

  A lab on another machine:
    python3 lab.py tunnel user@labhost                        (recommended) SSH-forward its ports here; env works unchanged
    python3 lab.py up --bind 0.0.0.0                          on the lab host: publish the management APIs beyond loopback
    python3 lab.py env --host labhost [--prepare]             on the client: variables for those APIs (no cluster APIs)

    -v / --verbose / --debug   log every health check attempt, command and prepared file (before or after the command)
    --tf-log LEVEL             (up, down) set TF_LOG for Terraform; its log can contain the lab's fixture credentials

Everything the lab generates (CA bundle, throwaway OCI key and profile, gcloud and az config folders) stays in .local/.
By default the commands only talk to the emulators on 127.0.0.1; see README.md for the isolation the lab expects.
"""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import start

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
ORDER = ("aws", "azure", "gcp", "oci")  # apply order; destroy runs in reverse
PORTS = {"aws": 4566, "azure": 4577, "gcp": 4588, "oci": 4599}  # management APIs (compose.yaml)
K3S_PORTS = {provider: range(base, base + 10)  # the k3s API host ports compose.yaml gives each emulator
             for provider, base in {"aws": 16443, "azure": 16543, "gcp": 16643, "oci": 16743}.items()}
HEALTH_PATHS = {  # tried in order; the first answer with HTTP 200 counts
    "aws": ("/_floci/health",),
    "azure": ("/_floci/health",),  # HTTP also for Azure (README)
    # floci-gcp 0.13.0 serves its own prefix (docker/healthcheck.sh: GET /_floci-gcp/health); README: /health is an alias
    "gcp": ("/_floci-gcp/health", "/health"),
    "oci": ("/_floci/health",),
}
LOOPBACK = "127.0.0.1"
AWS_CLUSTERS = ("klotse-eks-1", "klotse-eks-2")  # aws/main.tf local.clusters
AWS_ACTIVE_TIMEOUT = 180  # seconds for imported EKS clusters to become ACTIVE (Floci took about 15)
LAB_NAMESPACE = "klotse793"  # compose.yaml's FLOCI_*_DOCKER_RESOURCE_NAMESPACE: this lab only
LAB_LABEL = f"label=floci_namespace={LAB_NAMESPACE}"
LAB_NAME = re.compile(rf"floci-[a-z0-9]+-{LAB_NAMESPACE}-")  # Floci's own object names, e.g. floci-aws-klotse793-eks-…
STATE_FILES = ("terraform.tfstate", "terraform.tfstate.backup", ".terraform.tfstate.lock.info", "lab.tfplan")
AZURE_CA_URL = f"http://{LOOPBACK}:4577/_floci/tls-cert"
# az CLI in the lab's private AZURE_CONFIG_DIR: Floci Azure as a registered cloud and its seeded test service principal
# (floci-az docs/services/entra.md; the same fixture values as azure/main.tf). Public test values, not secrets.
AZ_CLOUD = "FlociLab"
AZ_TENANT = "00000000-0000-0000-0000-000000000002"
AZ_CLIENT = "11111111-1111-1111-1111-111111111111"
AZ_SECRET = "floci-az-dev-secret"
AZ_SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
VERBOSE = False  # -v/--verbose/--debug: log each health attempt, command and prepared file
HOST_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})")
AWS_KEY_VALUE = re.compile(r"[A-Za-z0-9/+=]{1,128}")  # an access key id or secret: no whitespace, quotes or shell syntax
SSH_DESTINATION = re.compile(r"(?:[A-Za-z0-9._-]{1,64}@)?[A-Za-z0-9](?:[A-Za-z0-9._-]{0,252})")


def url_host(host):
    """`host` for use in a URL (IPv6 in brackets); SystemExit for anything that is not a host name or an IP address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not HOST_NAME.fullmatch(host or ""):
            raise SystemExit(f"Not a host name or IP address: {host!r}") from None
        return host
    return f"[{address}]" if address.version == 6 else str(address)


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def base_url(provider, host=LOOPBACK):
    return f"http://{url_host(host)}:{PORTS[provider]}"


def health_urls(provider, host=LOOPBACK):
    return tuple(base_url(provider, host) + path for path in HEALTH_PATHS[provider])


HEALTH = {provider: health_urls(provider) for provider in PORTS}
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


def debug(text):
    if VERBOSE:
        print(f"[lab:debug] {text}", file=sys.stderr, flush=True)


def run(argv, **kwargs):
    """subprocess.run with the command logged in verbose mode (the lab passes no secrets on command lines)."""
    debug("run: " + " ".join(shlex.quote(str(part)) for part in argv))
    return subprocess.run(argv, **kwargs)


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
        "aws_key": local / "aws-key.json",
    }


def parse_aws_key(key):
    """(id, secret) from {"id": ..., "secret": ...}, or None when either is missing or not a plain key value."""
    if isinstance(key, dict) and all(isinstance(key.get(k), str) and AWS_KEY_VALUE.fullmatch(key[k]) for k in ("id", "secret")):
        return key["id"], key["secret"]
    return None


def aws_key(path):
    """(id, secret) of the lab IAM user's access key that `up` saved, or the public test pair before that. Only that
    key signs EKS tokens Floci's webhook accepts; the test pair (the account root) gets 401 from the cluster APIs."""
    try:
        return parse_aws_key(json.loads(path.read_text())) or ("test", "test")
    except (OSError, ValueError):
        return "test", "test"


def save_aws_key(env, local=None):
    """Copy the lab IAM user's access key from the AWS root's Terraform output into .local/aws-key.json (private)."""
    out = terraform("aws", "output", "-json", "klotse_access_key", env=env, capture=True).stdout
    try:
        key = parse_aws_key(json.loads(out))
    except ValueError:
        key = None
    if not key:
        raise SystemExit("AWS: the klotse_access_key output is not an access key id and secret.")
    path = paths(private_dir(local or LOCAL))["aws_key"]
    write_private(path, json.dumps({"id": key[0], "secret": key[1]}))
    debug(f"saved the lab IAM user's access key {key[0]} to {path}")


def environment(local=LOCAL, host=LOOPBACK):
    """The variables Terraform and the cloud CLIs need to reach the emulators (none are real credentials). `host` is where
    the emulators answer: this computer (also through `lab.py tunnel`), or another one with `env --host`."""
    p = paths(local)
    key_id, key_secret = aws_key(p["aws_key"])  # the lab IAM user's key once `up` saved it (EKS tokens need it)
    gcp = base_url("gcp", "localhost" if is_loopback(host) else host)  # floci-gcp's documented form is localhost
    env = {}
    if p["roots"].is_file():  # written by `up`; never point a shell at a CA bundle that does not exist
        # Terraform azurerm (Go) and the az CLI (Python requests) trust Floci Azure's generated CA through these.
        env["SSL_CERT_FILE"] = env["REQUESTS_CA_BUNDLE"] = str(p["roots"])
    env |= {
        "AZURE_CONFIG_DIR": str(p["azure_config"]),
        # OCI: Terraform reads both TF_VAR_ values (it runs on the lab host); the OCI CLI reads the next two.
        "TF_VAR_oci_config_file": str(p["oci_config"]),
        "TF_VAR_CLIENT_HOST_OVERRIDES": OCI_OVERRIDE,
        "OCI_CLI_CONFIG_FILE": str(p["oci_config"]),
        "OCI_CLI_ENDPOINT": base_url("oci", host),
        "SUPPRESS_LABEL_WARNING": "True",
        # AWS CLI and SDKs.
        "AWS_ENDPOINT_URL": base_url("aws", host),
        "AWS_ACCESS_KEY_ID": key_id,
        "AWS_SECRET_ACCESS_KEY": key_secret,
        "AWS_DEFAULT_REGION": "us-east-1",
        # gcloud: a private config folder, the emulator's endpoints (floci-gcp docs/services/gke.md) and a fixture token.
        "CLOUDSDK_CONFIG": str(p["gcloud"]),
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER": gcp + "/container/",
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_SERVICEUSAGE": gcp + "/",
        "CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API": "false",
        "CLOUDSDK_CORE_PROJECT": "klotse-local",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE": str(p["gcp_token"]),
    }
    if not is_loopback(host):
        env["KLOTSE_INSECURE_NET"] = "true"  # Klotse allows plain http only to this computer otherwise (testing only)
    return env


def render(env, shell):
    if shell == "powershell":
        return "".join(f"$env:{name} = '{value.replace(chr(39), chr(39) * 2)}'\n" for name, value in env.items())
    return "".join(f"export {name}={shlex.quote(value)}\n" for name, value in env.items())


def wait_healthy(providers, timeout=300, opener=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
                 host=LOOPBACK, alive=None):
    """Wait until every provider's emulator answers its health URL on `host`. `alive()` (optional) is called each round
    and raises SystemExit when what the URLs depend on (an SSH tunnel) has gone away."""
    deadline = clock() + timeout
    waiting = list(providers)
    last = {}  # provider -> the last answer or error per URL, shown when it times out
    while waiting:
        if alive:
            alive()
        for provider in list(waiting):
            for url in health_urls(provider, host):
                try:
                    with opener(url, timeout=5) as answer:
                        result = f"HTTP {answer.status}"
                        healthy = answer.status == 200
                except urllib.error.HTTPError as error:
                    result, healthy = f"HTTP {error.code}", False
                except (urllib.error.URLError, OSError) as error:
                    result, healthy = f"{type(error).__name__}: {getattr(error, 'reason', error)}", False
                debug(f"health {provider}: GET {url} -> {result}")
                last.setdefault(provider, {})[url] = result
                if healthy:
                    waiting.remove(provider)
                    log(f"{provider} emulator is up ({url})")
                    break
        if waiting:
            if clock() > deadline:
                for provider in waiting:
                    for url, result in last.get(provider, {}).items():
                        log(f"{provider}: GET {url} -> {result}")
                raise SystemExit(f"Emulators did not become healthy: {', '.join(waiting)} "
                                 f"(see `docker compose -f compose.yaml logs {' '.join(waiting)}`; -v shows every attempt)")
            sleep(3)


def system_bundle():
    return next((Path(candidate) for candidate in SYSTEM_BUNDLES if Path(candidate).is_file()), None)


def oci_fingerprint(key):
    der = run(["openssl", "rsa", "-in", str(key), "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


AZ_SECRET_SLOT = "<floci-az test secret>"  # stands for AZ_SECRET in az_setup_steps, so logs never carry the value


def az_setup_steps():
    """The az commands that make the lab's private az folder use Floci: (argv, must succeed); the password is
    AZ_SECRET_SLOT, which setup_az replaces only in the command it runs. The cloud is registered
    again each time (unregister needs another active cloud first), with explicit endpoints: discovery alone gave an AD
    endpoint without the port. Instance discovery is off so MSAL does not ask login.microsoftonline.com about the
    authority (core.instance_discovery, for private clouds)."""
    return [
        (["az", "cloud", "set", "--name", "AzureCloud"], False),
        (["az", "cloud", "unregister", "--name", AZ_CLOUD], False),
        (["az", "cloud", "register", "--name", AZ_CLOUD,
          "--endpoint-resource-manager", f"https://localhost:{PORTS['azure']}/",
          "--endpoint-active-directory", f"https://localhost:{PORTS['azure']}",
          "--endpoint-active-directory-resource-id", "https://management.core.windows.net/"], True),
        (["az", "cloud", "set", "--name", AZ_CLOUD], True),
        (["az", "config", "set", "core.instance_discovery=false"], True),
        (["az", "login", "--service-principal", "--username", AZ_CLIENT, "--password", AZ_SECRET_SLOT,
          "--tenant", AZ_TENANT, "--output", "none"], True),
        (["az", "account", "set", "--subscription", AZ_SUBSCRIPTION], True),
    ]


def setup_az(env):
    """Sign the lab's az folder in to Floci Azure, so `az aks list` (Klotse's Add clusters from the cloud) works from
    the lab shell. Optional: without az, or when a step fails, it warns and the rest of the lab is unaffected."""
    if not shutil.which("az"):
        log("az not found: skipped the Azure CLI sign-in (Klotse lists AKS clusters through az; README.md: Azure CLI).")
        return False
    for argv, required in az_setup_steps():
        debug("run: " + " ".join(shlex.quote(part) for part in argv))
        command = [AZ_SECRET if part == AZ_SECRET_SLOT else part for part in argv]
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        if result.returncode and required:
            log(f"WARNING: `{' '.join(argv[:3])}` failed (exit {result.returncode}); az is not signed in to Floci "
                "Azure. README.md: Azure CLI has the commands to run by hand.")
            return False
    log(f"az signed in to Floci Azure (cloud {AZ_CLOUD}, subscription {AZ_SUBSCRIPTION}) in .local/azure.")
    return True


def prepare(providers, local=LOCAL, fetch=urllib.request.urlopen, host=LOOPBACK, need_openssl=True):
    """Create the lab-only files under .local/ that the chosen providers need. Existing files are kept. On a client of a
    remote lab (need_openssl=False) a missing openssl skips the OCI profile with a warning instead of failing."""
    p = paths(local)
    private_dir(local)
    private_dir(p["gcloud"])
    private_dir(p["azure_config"])
    if not p["gcp_token"].exists():
        write_private(p["gcp_token"], GCP_TOKEN + "\n")
    if "oci" in providers and not need_openssl and not p["oci_config"].exists() and not shutil.which("openssl"):
        log("openssl not found: the OCI CLI profile (.local/oci-config) was not created on this computer.")
        providers = [provider for provider in providers if provider != "oci"]
    if "azure" in providers:
        with fetch(base_url("azure", host) + "/_floci/tls-cert", timeout=10) as answer:
            ca = answer.read()
        write_private(p["azure_ca"], ca)
        bundle = system_bundle()
        roots = (bundle.read_bytes().rstrip(b"\n") + b"\n" if bundle else b"") + ca
        write_private(p["roots"], roots)
    elif not p["roots"].exists() and system_bundle():
        write_private(p["roots"], system_bundle().read_bytes())
    if "oci" in providers and not p["oci_config"].exists():
        if not p["oci_key"].exists():
            run(["openssl", "genrsa", "-out", str(p["oci_key"]), "2048"], check=True, capture_output=True)
            os.chmod(p["oci_key"], 0o600)
        write_private(p["oci_config"], OCI_PROFILE.format(fingerprint=oci_fingerprint(p["oci_key"]),
                                                         key=p["oci_key"].as_posix()))
    for name, path in p.items():
        debug(f"prepared {name}: {path} ({'present' if path.exists() else 'missing'})")


def terraform(provider, *args, env, capture=False):
    argv = ["terraform", f"-chdir={HERE / provider}", *args]
    return run(argv, check=True, env=env, text=True, capture_output=capture)


def eks_address(name):
    return f'aws_eks_cluster.lab["{name}"]'


def eks_status(env):
    """{cluster name: status} for the EKS clusters in the aws state, refreshed from the emulator first."""
    terraform("aws", "apply", "-refresh-only", "-auto-approve", "-input=false", env=env, capture=True)
    shown = show_json(terraform("aws", "show", "-json", env=env, capture=True).stdout)
    resources = (shown.get("values") or {}).get("root_module", {}).get("resources", [])
    clusters = (r.get("values") or {} for r in resources if r.get("type") == "aws_eks_cluster")
    return {values["name"]: values.get("status") for values in clusters if values.get("name")}


def show_json(text):
    try:
        return json.loads(text or "{}")
    except ValueError:
        raise SystemExit("AWS recovery: terraform show -json did not return JSON.") from None


def replaced_clusters(env):
    """Addresses of EKS clusters the saved plan would delete or replace: applying it after an import would recreate
    the cluster and hit the same 409."""
    plan = show_json(terraform("aws", "show", "-json", "lab.tfplan", env=env, capture=True).stdout)
    return [change.get("address") for change in plan.get("resource_changes", [])
            if change.get("type") == "aws_eks_cluster" and "delete" in change.get("change", {}).get("actions", [])]


def recover_aws(env, timeout=AWS_ACTIVE_TIMEOUT, sleep=None):
    """Finish an AWS apply that Floci answered with '409 Cluster already exists'. Floci 2.5.0 drops the connection of
    the first CreateCluster in a new VPC while it attaches itself to the VPC's Docker network; the SDK retries, and the
    retry finds the cluster the first attempt created. Import only the lab clusters missing from state (an import
    fails for a cluster Floci does not have), wait until they are ACTIVE, then plan and apply the rest. False when
    nothing is missing: the failure was something else."""
    state = terraform("aws", "state", "list", env=env, capture=True).stdout.split()
    missing = [name for name in AWS_CLUSTERS if eks_address(name) not in state]
    if not missing:
        return False
    for name in missing:
        log(f"AWS: importing {name}, created by a CreateCluster whose answer was lost (Floci 409, see README.md)")
        terraform("aws", "import", "-input=false", eks_address(name), name, env=env)
    deadline = time.monotonic() + timeout
    while True:
        status = eks_status(env)
        failed = sorted(name for name in AWS_CLUSTERS if status.get(name) == "FAILED")
        if failed:
            raise SystemExit(f"AWS: Floci reports {', '.join(failed)} FAILED; run down --purge and up again.")
        waiting = sorted(name for name in AWS_CLUSTERS if status.get(name) != "ACTIVE")
        if not waiting:
            break
        if time.monotonic() >= deadline:
            raise SystemExit(f"AWS: {', '.join(waiting)} not ACTIVE after {timeout}s "
                             f"({', '.join(f'{n}={status.get(n)}' for n in waiting)}).")
        debug(f"AWS: waiting for {', '.join(waiting)} to become ACTIVE")
        (sleep or time.sleep)(5)
    terraform("aws", "plan", "-input=false", "-out=lab.tfplan", env=env)
    replaced = replaced_clusters(env)
    if replaced:
        raise SystemExit(f"AWS: after the import the plan would replace {', '.join(replaced)}; not applied. Review "
                         "`terraform -chdir=aws plan` (README: AWS recovery).")
    terraform("aws", "apply", "-input=false", "lab.tfplan", env=env)
    return True


def lab_env(tf_log=None):
    env = os.environ.copy()
    for key in list(env):
        if key.lower().endswith("_proxy") or key in ("TF_LOG", "TF_LOG_PATH"):
            env.pop(key)  # README: no proxies or Terraform tracing in the lab, unless --tf-log asks for it
    env.update(environment())
    if tf_log:
        env["TF_LOG"] = tf_log
        log(f"TF_LOG={tf_log}: Terraform's log may contain the lab's fixture credentials; do not share it as is.")
    debug("lab variables: " + ", ".join(sorted(environment())))
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
    run(argv, check=True, env=env)


def bind_address(value):
    """The address compose.yaml publishes the management APIs on (KLOTSE_LAB_BIND), and the host to check them on."""
    try:
        address = ipaddress.ip_address(value or LOOPBACK)
    except ValueError:
        raise SystemExit(f"--bind takes an IP address of this computer, not {value!r}") from None
    if not address.is_loopback:
        log(f"WARNING: the emulators' management APIs will listen on {address}, reachable from other machines with fixture "
            "credentials; use this only on an isolated network (lab.py tunnel needs no --bind).")
    check = LOOPBACK if address.is_unspecified or address.is_loopback else str(address)
    return str(address), check


def require_tools(identities):
    """Refuse before anything starts when a command up runs is not on PATH (kubectl only with --identities, where
    identity.py runs it after every Terraform root was applied)."""
    missing = [tool for tool in ("docker", "terraform", *(("kubectl",) if identities else ())) if not shutil.which(tool)]
    if missing:
        raise SystemExit(f"Not found on PATH: {', '.join(missing)}. Install it (README.md: Prerequisites) and run up again.")


def up(args):
    providers = selected(args.providers)
    require_tools(args.identities)
    bind, check = bind_address(args.bind)
    os.environ["KLOTSE_LAB_BIND"] = bind  # compose.yaml: ${KLOTSE_LAB_BIND:-127.0.0.1}:<port>:<port>
    debug(f"providers: {', '.join(providers)}; bind {bind}; start.py checks the Docker engine and bridges, "
          "then runs docker compose up -d")
    start.main()
    wait_healthy(providers, timeout=args.timeout, host=check)
    prepare(providers, host=check)
    env = lab_env(args.tf_log)
    for provider in providers:
        log(f"terraform apply: {provider}")
        step = "init"
        try:
            terraform(provider, "init", "-input=false", "-lockfile=readonly", env=env)
            step = "plan"
            terraform(provider, "plan", "-input=false", "-out=lab.tfplan", env=env)
            step = "apply"
            terraform(provider, "apply", "-input=false", "lab.tfplan", env=env)
        except subprocess.CalledProcessError:
            try:
                if provider == "aws" and step == "apply" and recover_aws(env):  # only an apply can lose a CreateCluster
                    continue
            except subprocess.CalledProcessError as error:
                log("AWS recovery failed at: " + " ".join(str(part) for part in error.cmd[2:] if part)
                    if isinstance(error.cmd, (list, tuple)) else f"AWS recovery failed: {error}")
            if provider == "aws":
                log("AWS failed; if it reported '409 Cluster already exists', follow 'AWS: recover a cluster created before "
                    "a conflict response' in README.md, then run up again with --providers for the rest.")
            raise SystemExit(f"terraform failed for {provider}; the emulators are still running (lab.py down stops them).")
    if "aws" in providers:
        save_aws_key(env)
    if "azure" in providers:
        setup_az(env)
    if args.identities:
        for provider in providers:
            if not (LOCAL / f"{provider}.kubeconfig").exists():
                identity("create", provider, env)
    log("Lab is up. Variables for a shell: eval \"$(python3 lab.py env)\"" +
        ("" if args.identities else "; Klotse identities: python3 identity.py create <provider> (see IDENTITIES.md)"))


def docker_ids(kind):
    """Names of this lab's containers, volumes or networks: those labelled with its own Floci namespace, and those
    Floci names after it without a label (the k3s data volumes, floci-aws-klotse793-eks-<cluster>). None when Docker
    could not list them (unknown is not the same as none)."""
    listing = ["docker", *(["ps", "-a"] if kind == "container" else [kind, "ls"])]
    name = "{{.Names}}" if kind == "container" else "{{.Name}}"
    found = []
    for filter_ in (LAB_LABEL, f"name={LAB_NAMESPACE}"):  # name= is a substring match; LAB_NAME narrows it
        try:
            result = run([*listing, "--filter", filter_, "--format", name], capture_output=True, text=True, check=False)
        except OSError as error:
            log(f"Could not run docker: {error}")
            return None
        if result.returncode:
            log(f"Could not list the lab's Docker {kind}s (exit {result.returncode}).")
            return None
        found += [item for item in (line.split(",")[0] for line in result.stdout.split())  # ps: name,link/alias
                  if item not in found and (filter_ == LAB_LABEL or LAB_NAME.match(item))]
    return found


def purge():
    """Remove everything this lab left in Docker and its local Terraform state, whatever the destroy managed.
    Floci starts the cluster sidecars (and AWS's VPC container) itself through the Docker socket, so compose never
    sees them; a cluster missing from Terraform state (the AWS 409 case) is never destroyed either."""
    try:
        run(["docker", "compose", "-f", str(HERE / "compose.yaml"), "down", "--volumes", "--remove-orphans"], check=False)
    except OSError as error:
        raise SystemExit(f"Could not run docker ({error}); nothing was purged.") from None
    leftovers = []
    for kind, remove in (("container", ["rm", "-f", "-v"]), ("volume", ["volume", "rm", "-f"]),
                         ("network", ["network", "rm"])):
        ids = docker_ids(kind)
        if ids:
            log(f"Removing {len(ids)} lab {kind}(s): {' '.join(ids)}")
            run(["docker", *remove, *ids], check=False)
        remaining = docker_ids(kind)
        leftovers += [f"{kind}s (could not list)"] if remaining is None else [f"{kind} {item}" for item in remaining]
    if leftovers:
        raise SystemExit("Some lab Docker objects remain (" + ", ".join(leftovers) + "); local Terraform state was "
                         "kept. Remove them, then run down --purge again.")
    for provider in ORDER:
        for name in STATE_FILES:
            (HERE / provider / name).unlink(missing_ok=True)
        (LOCAL / f"{provider}.kubeconfig").unlink(missing_ok=True)  # its credentials died with the clusters
    paths()["aws_key"].unlink(missing_ok=True)  # so is the IAM user
    log("Purged: no lab containers, volumes or networks remain; Terraform state, plans and lab kubeconfigs deleted.")


def down(args):
    if args.purge and args.providers:
        raise SystemExit("--purge removes the whole lab; it cannot be combined with --providers.")
    if args.purge:
        # No identity removal or terraform destroy: every emulator keeps its state in Docker, so removing the lab's
        # Docker objects removes the clusters too, and a destroy can hang for minutes (AWS VPC DependencyViolation
        # while clusters created before a 409 are missing from state).
        purge()
        return
    providers = selected(args.providers)
    env = lab_env(args.tf_log)
    failed = []
    for provider in reversed(providers):
        if (LOCAL / f"{provider}.kubeconfig").exists():
            try:
                identity("remove", provider, env)
            except subprocess.CalledProcessError:  # a broken cluster cannot revoke; destroying it still removes access
                log(f"Could not remove the {provider} lab identity; destroying its clusters anyway.")
                failed.append(provider)
        try:
            if (HERE / provider / ".terraform").is_dir():
                log(f"terraform destroy: {provider}")
                terraform(provider, "destroy", "-input=false", "-auto-approve", env=env)
                if provider == "aws":
                    paths()["aws_key"].unlink(missing_ok=True)  # the destroy removed its IAM user
        except subprocess.CalledProcessError:
            if provider not in failed:
                failed.append(provider)
    if failed:
        raise SystemExit(f"Destroy failed for {', '.join(failed)}; the emulators were left running so you can retry "
                         "(or down --purge to remove everything).")
    if set(providers) == set(ORDER):
        run(["docker", "compose", "-f", str(HERE / "compose.yaml"), "down", "--volumes"], check=True)
        log("Emulators stopped. Check README.md 'Tear down' for leftover containers before deleting state.")
    else:
        log("Only some providers were destroyed; the emulators are still running.")


REMOTE_NOTES = (
    "Variables point at the emulators on {host}. They must listen there (on the lab host: lab.py up --bind 0.0.0.0).",
    "KLOTSE_INSECURE_NET=true is set: Klotse allows plain http to other hosts only with it (testing only).",
    "Cluster APIs are not covered: the emulators report them as localhost:<port> with certificates for localhost, and "
    "Klotse's GCP emulator import uses its test token only for clusters on this computer. Use `lab.py tunnel` for those.",
)


def env_command(args):
    host = args.host or LOOPBACK
    url_host(host)  # refuse anything that is not a host name or address before it reaches a URL
    if args.prepare:
        prepare(list(ORDER), host=host, need_openssl=False)
    if not paths()["roots"].is_file():
        log("No .local/roots.pem yet (run `lab.py up`, or env --prepare): SSL_CERT_FILE and REQUESTS_CA_BUNDLE are left out.")
    if not is_loopback(host):
        for note in REMOTE_NOTES:
            log(note.format(host=host))
    sys.stdout.write(render(environment(host=host), args.shell))


def tunnel_argv(destination, providers, ssh_port=None, identity_file=None):
    """ssh -N with one -L per management port and per k3s API port of each provider, all on 127.0.0.1 at both ends, so
    every address the lab prints (and the localhost certificates) work unchanged on this computer."""
    if not SSH_DESTINATION.fullmatch(destination or ""):
        raise SystemExit(f"Not an SSH destination ([user@]host or an ssh config alias): {destination!r}")
    argv = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30"]
    if ssh_port:
        argv += ["-p", str(int(ssh_port))]
    if identity_file:
        argv += ["-i", str(identity_file)]
    for provider in providers:
        for port in (PORTS[provider], *K3S_PORTS[provider]):
            argv += ["-L", f"{LOOPBACK}:{port}:{LOOPBACK}:{port}"]
    return argv + ["--", destination]


def tunnel(args, popen=subprocess.Popen):
    providers = selected(args.providers)
    argv = tunnel_argv(args.destination, providers, args.port, args.identity_file)
    if args.print:
        print(" ".join(shlex.quote(part) for part in argv))
        return
    debug("run: " + " ".join(shlex.quote(part) for part in argv))
    process = popen(argv)

    def alive():
        if process.poll() is not None:
            raise SystemExit(f"ssh exited with status {process.returncode} (a local port in use, or the login failed; -v "
                             "shows the command)")

    try:
        wait_healthy(providers, timeout=args.timeout, alive=alive)
        prepare(providers, need_openssl=False)
        if "azure" in providers:
            setup_az(lab_env())  # this computer's own az folder, through the forwarded port
        log(f"Tunnel to {args.destination} is up. In another shell: eval \"$(python3 lab.py env)\". Copy the Klotse "
            "kubeconfigs from the lab host's labs/floci/.local/<provider>.kubeconfig, and .local/aws-key.json for EKS imports. "
            "Ctrl-C closes the tunnel.")
        process.wait()
    except KeyboardInterrupt:
        log("Closing the tunnel.")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


def main(argv=None):
    global VERBOSE
    common = argparse.ArgumentParser(add_help=False)  # accepted before or after the command: lab.py -v up, lab.py up -v
    common.add_argument("-v", "--verbose", "--debug", dest="verbose", action="store_true", default=argparse.SUPPRESS,
                        help="log every health check attempt, command run and prepared file")
    tf = argparse.ArgumentParser(add_help=False)
    tf.add_argument("--tf-log", metavar="LEVEL", choices=("TRACE", "DEBUG", "INFO", "WARN", "ERROR"),
                    help="set TF_LOG for Terraform (its log can contain the lab's fixture credentials)")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     parents=[common])
    commands = parser.add_subparsers(dest="command", required=True)
    up_parser = commands.add_parser("up", parents=[common, tf], help="start the emulators and apply the Terraform roots")
    up_parser.add_argument("--providers", help="comma-separated subset of aws,azure,gcp,oci (default: all)")
    up_parser.add_argument("--identities", action="store_true", help="also create the Klotse lab kubeconfigs (identity.py)")
    up_parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for the emulators (default 300)")
    up_parser.add_argument("--bind", metavar="ADDRESS", help="publish the management APIs on this address instead of "
                           "127.0.0.1 (for env --host from another machine; prefer lab.py tunnel)")
    down_parser = commands.add_parser("down", parents=[common, tf], help="remove identities, destroy the roots and stop the emulators")
    down_parser.add_argument("--providers", help="comma-separated subset; the emulators stop only when all are destroyed")
    down_parser.add_argument("--purge", action="store_true", help="skip identities and Terraform: remove every Docker "
                             "container, volume and network of the lab's namespace (the emulators keep their state "
                             "there), then delete the local Terraform state, plans and lab kubeconfigs")
    env_parser = commands.add_parser("env", parents=[common], help="print the lab variables for a shell")
    env_parser.add_argument("--shell", choices=("sh", "powershell"), default="sh")
    env_parser.add_argument("--host", help="the emulators run on this host (started there with up --bind); "
                            "default: this computer, also through lab.py tunnel")
    env_parser.add_argument("--prepare", action="store_true", help="create .local/ on this computer first "
                            "(Azure CA from the host, gcloud folder and token, OCI profile when openssl is installed)")
    tunnel_parser = commands.add_parser("tunnel", parents=[common], help="forward a remote lab's ports to this computer over SSH")
    tunnel_parser.add_argument("destination", help="[user@]host or an ssh config alias of the lab host")
    tunnel_parser.add_argument("--providers", help="comma-separated subset of aws,azure,gcp,oci (default: all)")
    tunnel_parser.add_argument("-p", "--port", type=int, help="SSH port of the lab host")
    tunnel_parser.add_argument("-i", "--identity-file", help="SSH private key file")
    tunnel_parser.add_argument("--timeout", type=int, default=60, help="seconds to wait for the emulators through the tunnel")
    tunnel_parser.add_argument("--print", action="store_true", help="only print the ssh command")
    args = parser.parse_args(argv)
    VERBOSE = getattr(args, "verbose", False)
    debug(f"lab directory {HERE}; Python {sys.version.split()[0]}")
    {"up": up, "down": down, "env": env_command, "tunnel": tunnel}[args.command](args)


if __name__ == "__main__":
    main()
