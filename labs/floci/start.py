"""Start the lab; warn when Docker defaults would publish k3s ports beyond loopback."""

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

EXPOSED = ("WARNING: {what} publishes ports on {ip}, not only on 127.0.0.1, so the lab's k3s APIs "
           "(admin access through any token) can be reachable from other machines. Recommended: a dedicated "
           "Docker daemon with \"ip\": \"127.0.0.1\" and default-network-opts host_binding_ipv4 127.0.0.1 "
           "(see README.md), or a firewall. Continuing.")


def warn(text):
    print(text, file=sys.stderr)


def engine_host():
    # DOCKER_CONTEXT takes precedence over DOCKER_HOST. Never start a lab on
    # a remote SSH/TCP engine even if that engine reports loopback bindings.
    host = None if os.environ.get("DOCKER_CONTEXT") else os.environ.get("DOCKER_HOST")
    if not host:
        host = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    return host


def bridge_bind_ip(network):
    option = '{{index .Options "com.docker.network.bridge.host_binding_ipv4"}}'
    return subprocess.run(
        ["docker", "network", "inspect", network, "--format", option],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def main():
    host = engine_host()
    if not host.startswith(("unix:///", "npipe:////./pipe/")):
        raise SystemExit("Use a local Docker Unix socket or Windows named pipe, not a remote engine.")
    default_ip = bridge_bind_ip("bridge")
    if default_ip != "127.0.0.1":
        warn(EXPOSED.format(what="Docker's default bridge", ip=default_ip or "all interfaces"))

    # EKS may publish on a bridge it creates for the emulated VPC rather
    # than on Docker's default bridge or the Compose network.
    probe = f"klotse793-bind-probe-{uuid4().hex}"
    subprocess.run(["docker", "network", "create", "--driver", "bridge", probe],
                   check=True, capture_output=True)
    try:
        probe_ip = bridge_bind_ip(probe)
        if probe_ip != "127.0.0.1":
            warn(EXPOSED.format(what="A new Docker bridge network (default-network-opts)", ip=probe_ip or "all interfaces"))
    finally:
        subprocess.run(["docker", "network", "rm", probe], check=True, capture_output=True)

    env = os.environ.copy()
    if host.startswith("unix:///"):
        env["KLOTSE_LAB_DOCKER_SOCKET"] = host.removeprefix("unix://")
    subprocess.run(
        ["docker", "compose", "-f", str(Path(__file__).with_name("compose.yaml")), "up", "-d"],
        check=True, env=env,
    )


if __name__ == "__main__":
    main()
