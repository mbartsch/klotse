"""Check both lab contexts through Klotse's Kubernetes client (no Qt required)."""

import argparse
from pathlib import Path

from identity import PROVIDERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=PROVIDERS)
    args = parser.parse_args()
    config = Path(__file__).with_name(".local") / f"{args.provider}.kubeconfig"
    if not config.is_file():
        raise SystemExit(f"Create credentials first: missing {config.name}.")

    from klotse.k8s.client import KubeService

    failed = False
    for index in (1, 2):
        context = f"klotse-{PROVIDERS[args.provider]}-{index}"
        service = KubeService(str(config))
        try:
            service.connect(context)
            nodes = service.list_nodes()
            pods = service.list_pods()
            if not nodes:
                raise RuntimeError("No Kubernetes nodes")
        except Exception:
            # Do not print exceptions containing data from a kubeconfig or API.
            print(f"{context}: Klotse connection or resource read failed.")
            failed = True
        else:
            ready = sum(n.is_ready for n in nodes)
            print(f"{context}: {ready}/{len(nodes)} nodes Ready; {len(pods)} pods listed.")
        finally:
            if service.is_connected():
                service.api_client().close()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
