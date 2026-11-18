"""Start the lab only when Docker defaults keep k3s ports on loopback."""

import os
from pathlib import Path
import subprocess


def local_engine():
    # DOCKER_CONTEXT takes precedence over DOCKER_HOST. Never start a lab on
    # a remote SSH/TCP engine even if that engine reports loopback bindings.
    host = None if os.environ.get("DOCKER_CONTEXT") else os.environ.get("DOCKER_HOST")
    if not host:
        host = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    return host.startswith(("unix:///", "npipe:////./pipe/"))


def main():
    if not local_engine():
        raise SystemExit("Use a local Docker Unix socket or Windows named pipe, not a remote engine.")
    option = '{{index .Options "com.docker.network.bridge.host_binding_ipv4"}}'
    result = subprocess.run(
        ["docker", "network", "inspect", "bridge", "--format", option],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip() != "127.0.0.1":
        raise SystemExit("Unsafe Docker default bridge: configure a dedicated local daemon "
                         "with default published IP 127.0.0.1 before launching the lab.")
    subprocess.run(
        ["docker", "compose", "-f", str(Path(__file__).with_name("compose.yaml")), "up", "-d"],
        check=True,
    )


if __name__ == "__main__":
    main()
