"""Issue short-lived Kubernetes credentials for a pair of local lab clusters."""

import argparse
from base64 import b64encode
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from tempfile import TemporaryDirectory

PROVIDERS = {"aws": "eks", "azure": "aks", "gcp": "gke", "oci": "oke"}


def kubectl(config, *args, manifest=None):
    env = os.environ.copy()
    for key in list(env):
        if key.lower().endswith("_proxy"):
            del env[key]
    try:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", str(config), "--request-timeout=15s", *args],
            input=json.dumps(manifest) if manifest is not None else None,
            capture_output=True, text=True, env=env, check=False,
        )
    except FileNotFoundError:
        raise SystemExit("kubectl is not on PATH; install it (README.md: Prerequisites) and run again.") from None
    if result.returncode:
        # Neither kubeconfig material nor server errors belong in the lab log.
        raise SystemExit(f"kubectl {args[0]} failed (exit {result.returncode}).")
    return result.stdout


def docker(*args):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit("docker is not on PATH; install it (README.md: Prerequisites) and run again.") from None
    if result.returncode:
        raise SystemExit(f"docker {args[0]} failed (exit {result.returncode}).")
    return result.stdout


def bootstrap(service, resource_id):
    ids = docker(
        "ps", "-q", "--filter", "label=floci_namespace=klotse793",
        "--filter", f"label=io.floci.service={service}",
        "--filter", f"label=io.floci.resource-id={resource_id}",
    ).split()
    if len(ids) != 1:
        raise SystemExit(f"Expected one running lab k3s container for {resource_id}.")
    container = json.loads(docker("inspect", ids[0]))[0]
    ports = container["NetworkSettings"]["Ports"].get("6443/tcp")
    if not ports:
        raise SystemExit("The lab k3s container publishes no API port.")
    if any(item.get("HostIp") != "127.0.0.1" for item in ports):
        print(f"WARNING: the k3s API of {resource_id} is published beyond 127.0.0.1 and can be reachable from other "
              "machines; see README.md for a loopback-only Docker daemon. Klotse still connects through 127.0.0.1.",
              file=sys.stderr)
    port = int(ports[0]["HostPort"])
    base = {"eks": 16443, "aks": 16543, "gke": 16643, "oke": 16743}[service]
    if not base <= port <= base + 9:
        raise SystemExit("k3s published port is outside this provider's lab range.")
    data = json.loads(docker(
        "exec", ids[0], "kubectl", "config", "view",
        "--kubeconfig", "/etc/rancher/k3s/k3s.yaml",
        "--minify", "--flatten", "--raw", "-o", "json",
    ))
    cluster = data["clusters"][0]["cluster"]
    if (not cluster.get("certificate-authority-data") or cluster.get("proxy-url")
            or cluster.get("insecure-skip-tls-verify")):
        raise SystemExit("Expected k3s bootstrap config with a CA and no proxy.")
    if any(key in data["users"][0]["user"] for key in ("exec", "auth-provider")):
        raise SystemExit("Expected a static k3s bootstrap identity.")
    cluster["server"] = f"https://127.0.0.1:{port}"
    return data, cluster


def write_config(target, data):
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(data, stream)


def provision(config, identity, username):
    # Never use a bearer token here: some Floci webhooks grant administrator
    # access even to an expired or deleted Kubernetes ServiceAccount token.
    key = config.with_suffix(".key")
    request = config.with_suffix(".csr")
    result = subprocess.run(
        ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj",
         f"/CN={username}", "-keyout", str(key), "-out", str(request)],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise SystemExit("Could not generate a local client certificate request.")
    csr = {
        "apiVersion": "certificates.k8s.io/v1", "kind": "CertificateSigningRequest",
        "metadata": {"name": username},
        "spec": {"request": b64encode(request.read_bytes()).decode("ascii"),
                 "signerName": "kubernetes.io/kube-apiserver-client", "usages": ["client auth"],
                 "expirationSeconds": 28800},
    }
    kubectl(config, "create", "-f", "-", manifest=csr)
    try:
        kubectl(config, "certificate", "approve", username)
        for _ in range(30):
            status = json.loads(kubectl(config, "get", "csr", username, "-o", "json"))["status"]
            cert = status.get("certificate")
            if cert:
                break
            time.sleep(1)
        else:
            raise SystemExit("The lab k3s signer did not issue a client certificate.")
    finally:
        kubectl(config, "delete", "csr", username, "--ignore-not-found")

    role = {
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
        "metadata": {"name": identity},
        "rules": [
            {"apiGroups": [""], "resources": ["nodes", "namespaces", "pods", "services"],
             "verbs": ["get", "list", "watch"]},
        ],
    }
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
        "metadata": {"name": identity},
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": identity},
        "subjects": [{"kind": "User", "name": username, "apiGroup": "rbac.authorization.k8s.io"}],
    }
    kubectl(config, "apply", "-f", "-", manifest={
        "apiVersion": "v1", "kind": "List", "items": [role, binding],
    })
    return {"client-certificate-data": cert,
            "client-key-data": b64encode(key.read_bytes()).decode("ascii")}


def revoke(config, identity):
    kubectl(config, "delete", "clusterrolebinding,clusterrole", identity, "--ignore-not-found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "reset", "remove"))
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument("--oci-ids", nargs=2, metavar=("OKE_1_ID", "OKE_2_ID"),
                        help="Terraform cluster IDs, in klotse-oke-1, klotse-oke-2 order")
    parser.add_argument("--directory", type=Path, default=Path(__file__).with_name(".local"))
    args = parser.parse_args()
    if (args.provider == "oci") != bool(args.oci_ids):
        parser.error("--oci-ids is required for OCI only")
    service = PROVIDERS[args.provider]
    names = [f"klotse-{service}-{i}" for i in (1, 2)]
    resource_ids = args.oci_ids or names
    if len(set(resource_ids)) != 2:
        parser.error("The two cluster IDs must differ")
    target = args.directory / f"{args.provider}.kubeconfig"
    if args.action == "create" and target.exists():
        raise SystemExit("Credentials already exist; use reset to rotate or remove to revoke.")

    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.directory) as scratch:
        configs, clusters = [], []
        for name, resource_id in zip(names, resource_ids):
            admin, cluster = bootstrap(service, resource_id)
            config = Path(scratch) / name
            write_config(config, admin)
            configs.append(config)
            clusters.append(cluster)
        if clusters[0]["server"] == clusters[1]["server"]:
            raise SystemExit("The two lab clusters must target distinct APIs.")

        identity = f"klotse-lab-{args.provider}"
        if args.action in ("reset", "remove"):
            for config in configs:
                revoke(config, identity)
            target.unlink(missing_ok=True)
        if args.action == "remove":
            return

        lab = {"apiVersion": "v1", "kind": "Config", "users": [], "clusters": [], "contexts": []}
        username = f"{identity}-{uuid4().hex[:12]}"
        for name, cluster, config in zip(names, clusters, configs):
            lab["clusters"].append({"name": name, "cluster": cluster})
            lab["users"].append({"name": name, "user": provision(config, identity, username)})
            lab["contexts"].append({"name": name, "context": {"cluster": name, "user": name}})
        lab["current-context"] = names[0]
        candidate = Path(scratch) / "candidate"
        write_config(candidate, lab)
        for name in names:
            result = kubectl(candidate, "auth", "whoami", "--context", name, "-o", "json")
            actual = json.loads(result)["status"]["userInfo"]["username"]
            if actual != username:
                raise SystemExit(f"{name} did not authenticate as the lab user.")
            kubectl(candidate, "get", "nodes", "--context", name, "-o", "name")
        write_config(target, lab)
    print(f"Created {target}; add this kubeconfig to Klotse.")


if __name__ == "__main__":
    main()
