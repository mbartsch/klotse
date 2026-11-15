"""Google Cloud (GKE) plugin: signs in to Google Cloud for a GKE cluster's nodes, in a terminal.

A GKE kubeconfig runs `gke-gcloud-auth-plugin`, which uses the account `gcloud` is signed in with. Klotse does not run the
sign-in itself (a browser is needed): the action opens a terminal with `gcloud auth login`, and the user connects again."""
from klotse.plugins import Action, Message, Plugin, Setting, Terminal, parse_environment
from . import clusters

HELP_GCP = "Set this when the cluster's nodes do not report their cloud in the providerID (a local emulator, a custom node pool). auto uses the providerID check; gcp accepts this cluster's nodes without it."
GCE_PREFIX = "gce://"  # the providerID of a node that runs on Compute Engine (GKE nodes do)


def gcloud_login_argv():
    """argv that signs in to Google Cloud with the gcloud CLI, to run in a terminal."""
    return ("gcloud", "auth", "login")


def is_gke_node(raw):
    """Whether a Node manifest is a Compute Engine (GKE) node, from its providerID."""
    provider = str(((raw or {}).get("spec") or {}).get("providerID") or "")
    return provider.startswith(GCE_PREFIX)


def cloud_forced(settings):
    """True when this cluster's `cloud` setting names Google Cloud (then the providerID is not checked)."""
    return str((settings or {}).get("cloud") or "auto").strip().lower() == "gcp"


class GcpPlugin(Plugin):
    id = "gcp"
    name = "Google Cloud (GKE) Plugin"
    version = "0.1"
    requires_api = 12
    description = "Signs in to Google Cloud (gcloud auth login) for the nodes of a GKE cluster, in a terminal."
    actions = (
        Action("sign-in", "Sign in to Google Cloud for this cluster (gcloud auth login)…", kinds=("Node",)),
    )
    cluster_settings = (
        Setting("cloud", "Cloud of this cluster", "auto", pattern=r"auto|gcp", help=HELP_GCP),
    )

    settings = (
        Setting("extra_environment", "Extra variables for commands and terminals", "", kind="multiline",
                section="Commands and terminals",
                help="One NAME=value per line, added to the environment of the kubectl and helm commands and the terminals Klotse starts for a cluster, for example CLOUDSDK_ACTIVE_CONFIG_NAME=work. Saved as plain text in the settings file: do not put secrets here. It does not change how Klotse itself connects to the cluster."),
    )

    cluster_sources = (clusters.SOURCE,)

    def cluster_defaults(self, source, env):
        return clusters.defaults(env) if source.key == "gke" else {}

    def list_clusters(self, source, scope, run):
        if source.key != "gke":
            return super().list_clusters(source, scope, run)
        return clusters.list_clusters(scope, run)

    def cluster_credentials(self, source, cluster, scope, target, run):
        if source.key != "gke":
            return super().cluster_credentials(source, cluster, scope, target, run)
        clusters.cluster_credentials(cluster, scope, target, run)

    def process_environment(self, context, settings):
        return parse_environment(settings.get("extra_environment"))

    def run(self, request):
        if request.action != "sign-in":
            return None
        if not cloud_forced(request.settings) and not any(is_gke_node(resource.raw) for resource in request.resources):
            return Message("This node is not on Google Cloud: its providerID is not gce://.", "warning")
        return Terminal(gcloud_login_argv())


plugin = GcpPlugin()
