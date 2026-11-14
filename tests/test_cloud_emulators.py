"""Smoke tests against the Floci emulators, with the real provider CLIs. They run only in the cloud-emulators workflow, where the
emulators and the CLIs are set up (KLOTSE_EMULATORS=1). Without that variable they are skipped, so a developer machine needs
neither. A missing CLI fails the test: in that job it means the setup is broken, not that there is nothing to test.

They check the environment Klotse's cloud features rely on (the CLIs reach the emulators, the commands write kubeconfig
files and answer JSON). Klotse's own code is tested against the emulators in the follow-up once its feature PRs are merged."""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(os.environ.get("KLOTSE_EMULATORS") != "1",
                                reason="needs the Floci emulators and provider CLIs (cloud-emulators workflow)")

CLUSTER = "klotse-ci"


def cli(name, *args, env=None, check=True):
    """Run a provider CLI with argv and return its standard output. A missing CLI fails the test."""
    if shutil.which(name) is None:
        pytest.fail(f"{name} is not on the PATH: the cloud-emulators workflow installs it")
    done = subprocess.run([name, *args], capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, **(env or {})))  # nosec B603 - fixed argv, no shell
    if check and done.returncode != 0:
        pytest.fail(f"{name} {args[0] if args else ''} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def test_the_aws_cli_reaches_the_emulator_and_reports_its_account():
    identity = json.loads(cli("aws", "sts", "get-caller-identity", "--output", "json"))
    assert identity["Account"] == "000000000000"


def test_an_eks_cluster_in_the_emulator_gives_a_kubeconfig_entry_through_the_aws_cli(tmp_path):
    vpc = cli("aws", "ec2", "create-vpc", "--cidr-block", "10.42.0.0/16", "--query", "Vpc.VpcId", "--output", "text").strip()
    subnet = cli("aws", "ec2", "create-subnet", "--vpc-id", vpc, "--cidr-block", "10.42.1.0/24",
                 "--availability-zone", "us-east-1a", "--query", "Subnet.SubnetId", "--output", "text").strip()
    try:
        cli("aws", "eks", "create-cluster", "--name", CLUSTER, "--role-arn", "arn:aws:iam::000000000000:role/klotse-ci",
            "--resources-vpc-config", f"subnetIds={subnet}")
        status = ""
        for _ in range(60):
            status = cli("aws", "eks", "describe-cluster", "--name", CLUSTER, "--query", "cluster.status", "--output", "text").strip()
            if status == "ACTIVE":
                break
            time.sleep(1)
        assert status == "ACTIVE"
        names = json.loads(cli("aws", "eks", "list-clusters", "--output", "json"))["clusters"]
        assert CLUSTER in names
        target = tmp_path / "kubeconfig.yaml"
        cli("aws", "eks", "update-kubeconfig", "--name", CLUSTER, "--region", "us-east-1", "--kubeconfig", str(target))
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert any(CLUSTER in ctx["name"] for ctx in data["contexts"])
        assert data["clusters"][0]["cluster"]["server"].startswith("https://")
    finally:
        cli("aws", "eks", "delete-cluster", "--name", CLUSTER, check=False)


def test_the_oci_cli_lists_clusters_through_floci_oci(tmp_path):
    key = tmp_path / "key.pem"
    cli("openssl", "genrsa", "-out", str(key), "2048")
    tenancy = "ocid1.tenancy.oc1..flocilocaltenancy0000000000000000000000000000000000000000"
    config = tmp_path / "config"
    config.write_text(
        "[DEFAULT]\n"
        "user=ocid1.user.oc1..floci\n"
        "fingerprint=00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff\n"
        f"tenancy={tenancy}\n"
        "region=us-phoenix-1\n"
        f"key_file={key}\n", encoding="utf-8")
    config.chmod(0o600)  # the OCI CLI refuses a config file that other users can read
    done = subprocess.run(["oci", "ce", "cluster", "list", "--compartment-id", tenancy, "--region", "us-phoenix-1", "--all"],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, OCI_CLI_CONFIG_FILE=str(config)))  # nosec B603 - fixed argv, no shell
    assert done.returncode == 0, f"oci exit {done.returncode}: {(done.stderr or done.stdout).strip()[-600:]}"
    try:
        data = json.loads(done.stdout)
    except ValueError:
        pytest.fail(f"oci printed no JSON (stdout {done.stdout[:200]!r}; stderr {done.stderr[-400:]!r})")
    assert isinstance(data.get("data"), list)


@pytest.mark.parametrize("port", [4588, 4577])
def test_the_gcp_and_azure_emulators_answer_http(port):
    # Their CLIs are not exercised yet: gcloud needs the endpoint overrides and az needs TLS (see floci-gcp and floci-az).
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=20) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code  # an answer, even an error, shows the emulator is up
    assert status < 500
