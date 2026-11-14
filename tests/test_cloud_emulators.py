"""Smoke tests against the Floci emulators, with the real provider CLIs. They run only in the cloud-emulators workflow, where the
emulators and the CLIs are set up (KLOTSE_EMULATORS=1). Without that variable they are skipped, so a developer machine needs
neither. A missing CLI fails the test: in that job it means the setup is broken, not that there is nothing to test.

They check the environment Klotse's cloud features rely on (the CLIs reach the emulators, the commands write kubeconfig
files and answer JSON). Klotse's own code is tested against the emulators in the follow-up once its feature PRs are merged."""
import json
import os
import shlex
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
OCI_ENDPOINT = "http://127.0.0.1:4599"  # floci-oci, as in its README


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
    env = dict(os.environ, OCI_CLI_CONFIG_FILE=str(config), SUPPRESS_LABEL_WARNING="True")

    def oci(*args):
        done = subprocess.run(["oci", *args], capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                              env=env)  # nosec B603 - fixed argv, no shell
        return done

    # The command the Floci documentation uses first, with its explicit endpoint: it proves the CLI and the emulator agree.
    namespace = oci("os", "ns", "get", "--endpoint", OCI_ENDPOINT)
    assert namespace.returncode == 0 and "data" in namespace.stdout, (
        f"oci os ns get: exit {namespace.returncode}, stdout {namespace.stdout[:200]!r}, stderr {namespace.stderr[-400:]!r}")
    listed = oci("ce", "cluster", "list", "--compartment-id", tenancy, "--region", "us-phoenix-1", "--all", "--endpoint", OCI_ENDPOINT)
    assert listed.returncode == 0, f"oci ce cluster list: exit {listed.returncode}, stderr {listed.stderr[-400:]!r}"
    # The emulator answers 200 with an empty list, and the OCI CLI prints nothing for an empty list (seen in CI: the
    # debug log showed HTTP 200 and content-length 2, i.e. []). Any output that is not empty must be a JSON list.
    if listed.stdout.strip():
        try:
            data = json.loads(listed.stdout)
        except ValueError:
            pytest.fail(f"oci ce cluster list printed something that is not JSON: {listed.stdout[-400:]!r}")
        assert isinstance(data.get("data"), list)


def test_floci_az_env_connection_string_drives_the_az_storage_commands():
    """`eval $(floci az env)` exports AZURE_STORAGE_CONNECTION_STRING over plain http. The az CLI's storage commands use it;
    the ARM and login paths (which floci az setup registers over https) are not part of this test."""
    printed = cli("floci", "az", "env", "--endpoint", "http://127.0.0.1:4577", "--host", "127.0.0.1", "--shell", "bash",
                  "--no-color", "--no-emoji")
    line = next((ln for ln in printed.splitlines() if "AZURE_STORAGE_CONNECTION_STRING" in ln), "")
    assert line, f"floci az env printed no AZURE_STORAGE_CONNECTION_STRING: {printed[:300]!r}"
    connection = shlex.split(line.split("=", 1)[1])[0]
    assert connection.startswith("DefaultEndpointsProtocol=http;")
    env = {"AZURE_STORAGE_CONNECTION_STRING": connection}
    container = "klotse-ci"
    cli("az", "storage", "container", "create", "--name", container, env=env)
    try:
        names = json.loads(cli("az", "storage", "container", "list", "--output", "json", env=env))
        assert any(item["name"] == container for item in names)
    finally:
        cli("az", "storage", "container", "delete", "--name", container, env=env, check=False)


@pytest.mark.parametrize("port", [4588, 4577])
def test_the_gcp_and_azure_emulators_answer_http(port):
    # Their CLIs are not exercised yet: gcloud needs the endpoint overrides and az needs TLS (see floci-gcp and floci-az).
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=20) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code  # an answer, even an error, shows the emulator is up
    assert status < 500
