"""Start the lab only when Docker defaults keep k3s ports on loopback."""

from pathlib import Path
import subprocess


def main():
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
