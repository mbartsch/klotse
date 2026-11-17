"""Oracle Cloud (OKE) plugin: signs in to Oracle Cloud for an OKE cluster's nodes, in a terminal.

An OKE kubeconfig runs `oci ce cluster generate-token`, which uses the OCI config profile (~/.oci/config). Klotse does not run
the sign-in itself (a browser is needed): the action opens a terminal with `oci session authenticate` for that profile and
the cluster's region, and the user connects again."""
from klotse.plugins import Action, Message, Plugin, Setting, Terminal, parse_environment
from klotse.plugins.api import CloudError, SAFE_CLUSTER_VALUE, checked_value
from . import clusters

HELP_OCI = "Set this when the cluster's nodes do not report their cloud in the providerID (a local emulator, a custom node pool). auto uses the providerID check; oci accepts this cluster's nodes without it."
OCI_PROVIDER_PREFIXES = ("oci://", "ocid1.instance.")  # providerID of an OKE node
REGION_LABEL = "topology.kubernetes.io/region"


def oci_login_argv(region, profile):
    """argv that starts a browser sign-in to OCI for `region` and the config `profile`, to run in a terminal."""
    region = checked_value(region, clusters.REGION, "OCI region")
    profile = checked_value(profile, SAFE_CLUSTER_VALUE, "OCI config profile") or "DEFAULT"
    if not region:
        raise CloudError("The OCI region is required for sign-in.")
    return ("oci", "session", "authenticate", "--region", region, "--profile-name", profile)


def is_oke_node(raw):
    """Whether a Node manifest is an OCI (OKE) node, from its providerID."""
    provider = str(((raw or {}).get("spec") or {}).get("providerID") or "")
    return provider.startswith(OCI_PROVIDER_PREFIXES)


def cloud_forced(settings):
    """True when this cluster's `cloud` setting names Oracle Cloud (then the providerID is not checked)."""
    return str((settings or {}).get("cloud") or "auto").strip().lower() == "oci"


class OciPlugin(Plugin):
    id = "oci"
    name = "Oracle Cloud (OKE) Plugin (WIP)"
    version = "0.1"
    requires_api = 12
    description = "WIP: help appreciated. OKE CLI sign-in and cluster discovery. Browser authentication and reconnect require real OCI validation; node columns, pricing and cost cards are not implemented."
    actions = (
        Action("sign-in", "Sign in to Oracle Cloud for this cluster (oci session authenticate)…", kinds=("Node",)),
    )
    cluster_settings = (
        Setting("cloud", "Cloud of this cluster", "auto", pattern=r"auto|oci", help=HELP_OCI),
    )
    settings = (
        Setting("oci_profile", "OCI config profile", "DEFAULT",
                help="The profile in ~/.oci/config that the cluster's kubeconfig helper uses. The sign-in refreshes this profile's session token."),
        Setting("extra_environment", "Extra variables for commands and terminals", "", kind="multiline",
                section="Commands and terminals",
                help="One NAME=value per line, added to the environment of the kubectl and helm commands, the terminals Klotse starts and Klotse's own connection for a cluster, for example OCI_CLI_PROFILE=work. Saved as plain text in the settings file: do not put secrets here. Klotse's own connection to the cluster gets them too (the kubeconfig's sign-in helper), and they are never written to the kubeconfig or logs."),
    )

    cluster_sources = (clusters.SOURCE,)

    def cluster_defaults(self, source, env):
        return clusters.defaults(env) if source.key == "oke" else {}

    def list_clusters(self, source, scope, run):
        if source.key != "oke":
            return super().list_clusters(source, scope, run)
        return clusters.list_clusters(scope, run)

    def cluster_credentials(self, source, cluster, scope, target, run):
        if source.key != "oke":
            return super().cluster_credentials(source, cluster, scope, target, run)
        clusters.cluster_credentials(cluster, scope, target, run)

    def process_environment(self, context, settings):
        return parse_environment(settings.get("extra_environment"))

    def run(self, request):
        if request.action != "sign-in":
            return None
        nodes = list(request.resources) if cloud_forced(request.settings) else [resource for resource in request.resources if is_oke_node(resource.raw)]
        if not nodes:
            return Message("This node is not on Oracle Cloud: its providerID is not an OCI instance OCID.", "warning")
        labels = ((nodes[0].raw or {}).get("metadata") or {}).get("labels") or {}
        region = labels.get(REGION_LABEL, "")
        if not region:
            return Message("The node has no region label, so the OCI region for the sign-in is not known.", "warning")
        try:
            argv = oci_login_argv(region, str(request.settings.get("oci_profile") or ""))
        except CloudError as error:
            return Message(str(error), "warning")
        return Terminal(argv)


plugin = OciPlugin()
