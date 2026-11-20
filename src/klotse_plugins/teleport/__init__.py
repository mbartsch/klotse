"""Teleport plugin: adds the Kubernetes clusters a signed-in Teleport user may reach (File > Add clusters from the cloud…).

Listing runs `tsh kube ls --format=json`; adding runs `tsh kube login <name>` into a private kubeconfig that Klotse merges.
The user signs in with `tsh login` in a terminal first; Klotse does not sign in to Teleport."""
from klotse.plugins import Plugin
from . import clusters


class TeleportPlugin(Plugin):
    id = "teleport"
    name = "Teleport Plugin"
    version = "0.1"
    requires_api = 11
    description = ("Adds the Kubernetes clusters of a signed-in Teleport user with tsh (File > Add clusters from the cloud). "
                   "Sign in with tsh login in a terminal first. Not verified against a real Teleport cluster.")

    cluster_sources = (clusters.SOURCE,)

    def cluster_defaults(self, source, env):
        if source.key != "teleport":
            return {}
        proxy = (env.get("TELEPORT_PROXY") or "").strip()
        return {"proxy": proxy} if proxy else {}

    def list_clusters(self, source, scope, run):
        if source.key != "teleport":
            return super().list_clusters(source, scope, run)
        return clusters.list_clusters(scope, run)

    def cluster_credentials(self, source, cluster, scope, target, run):
        if source.key != "teleport":
            return super().cluster_credentials(source, cluster, scope, target, run)
        clusters.cluster_credentials(cluster, scope, target, run)


plugin = TeleportPlugin()
