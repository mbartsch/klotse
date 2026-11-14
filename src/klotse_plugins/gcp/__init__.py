"""Google Cloud (GKE) plugin: signs in to Google Cloud for a GKE cluster's nodes, in a terminal.

A GKE kubeconfig runs `gke-gcloud-auth-plugin`, which uses the account `gcloud` is signed in with. Klotse does not run the
sign-in itself (a browser is needed): the action opens a terminal with `gcloud auth login`, and the user connects again."""
from klotse.plugins import Action, Message, Plugin, Setting, Terminal

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
    requires_api = 10
    description = "Signs in to Google Cloud (gcloud auth login) for the nodes of a GKE cluster, in a terminal."
    actions = (
        Action("sign-in", "Sign in to Google Cloud for this cluster (gcloud auth login)…", kinds=("Node",)),
    )
    cluster_settings = (
        Setting("cloud", "Cloud of this cluster", "auto", pattern=r"auto|gcp", help=HELP_GCP),
    )

    def run(self, request):
        if request.action != "sign-in":
            return None
        if not cloud_forced(request.settings) and not any(is_gke_node(resource.raw) for resource in request.resources):
            return Message("This node is not on Google Cloud: its providerID is not gce://.", "warning")
        return Terminal(gcloud_login_argv())


plugin = GcpPlugin()
