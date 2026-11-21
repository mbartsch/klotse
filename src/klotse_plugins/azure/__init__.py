"""Azure (AKS) plugin: signs in to Azure for an AKS cluster's nodes, in a terminal.

An AKS kubeconfig usually runs `kubelogin` (Microsoft Entra ID), which reuses the account `az` is signed in with. Klotse does
not run the sign-in itself (a browser or a code prompt is needed): the action opens a terminal with `az login`, and the user
connects again. MFA is answered there."""
from klotse.plugins import Action, Column, Message, OpenUrl, Plugin, Setting, Terminal, parse_environment
from . import clusters
from .metadata import node_values, portal_url

AZURE_PROVIDER_PREFIX = "azure://"  # providerID of an AKS node: azure:///subscriptions/.../virtualMachines/...


def az_login_argv(tenant="", device_code=False):
    """argv that signs in to Azure with the az CLI, to run in a terminal. `tenant` (a tenant id or domain) is optional;
    `device_code` shows a code to enter in a browser instead of opening one (for terminals without a browser)."""
    return ("az", "login") + (("--tenant", tenant) if tenant else ()) + (("--use-device-code",) if device_code else ())


def is_aks_node(raw):
    """Whether a Node manifest is an Azure (AKS) node, from its providerID."""
    provider = str(((raw or {}).get("spec") or {}).get("providerID") or "")
    return provider.startswith(AZURE_PROVIDER_PREFIX)


def _flag(value):
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def cloud_forced(settings):
    """True when this cluster's `cloud` setting names Azure (then the providerID is not checked)."""
    return str((settings or {}).get("cloud") or "auto").strip().lower() == "azure"


class AzurePlugin(Plugin):
    id = "azure"
    name = "Azure (AKS) Plugin (WIP)"
    version = "0.1"
    requires_api = 14  # Klotse passes the cluster's one Cloud setting as settings["cloud"] (#818)
    cloud = "azure"
    description = "WIP: help appreciated. Node metadata, portal links, sign-in and cluster discovery. Pricing and cost cards are not implemented; real AKS verification is needed."
    columns = (
        Column("machine-type", "Azure VM size"),
        Column("capacity", "Azure capacity"),
        Column("node-image", "Azure node image"),
        Column("region", "Azure region"),
    )
    actions = (
        Action("portal", "Open node in Azure portal…", kinds=("Node",)),
        Action("sign-in", "Sign in to Azure for this cluster (az login)…", kinds=("Node",)),
    )
    settings = (
        Setting("azure_tenant", "Azure tenant (optional)", "",
                help="A tenant id or domain for az login, when the account belongs to more than one tenant. Empty uses the default."),
        Setting("azure_device_code", "Sign in with a device code", False, kind="bool",
                help="Shows a code to enter in a browser, for a terminal without a browser. Off opens the browser."),
        Setting("extra_environment", "Extra variables for commands and terminals", "", kind="multiline",
                section="Commands and terminals",
                help="One NAME=value per line, added to the environment of the kubectl and helm commands, the terminals Klotse starts and Klotse's own connection for a cluster, for example AZURE_CONFIG_DIR=/path/to/dir. Saved as plain text in the settings file: do not put secrets here. Klotse's own connection to the cluster gets them too (the kubeconfig's sign-in helper), and they are never written to the kubeconfig or logs."),
    )

    cluster_sources = (clusters.SOURCE,)

    def cluster_defaults(self, source, env):
        return clusters.defaults(env) if source.key == "aks" else {}

    def list_clusters(self, source, scope, run):
        if source.key != "aks":
            return super().list_clusters(source, scope, run)
        return clusters.list_clusters(scope, run)

    def cluster_credentials(self, source, cluster, scope, target, run):
        if source.key != "aks":
            return super().cluster_credentials(source, cluster, scope, target, run)
        clusters.cluster_credentials(cluster, scope, target, run)

    def process_environment(self, context, settings):
        return parse_environment(settings.get("extra_environment"))

    def enrich(self, request):
        if request.kind != "Node":
            return {}
        return {resource.key: node_values(resource.raw) for resource in request.resources
                if is_aks_node(resource.raw) or cloud_forced(request.settings)}

    def run(self, request):
        if request.action == "portal":
            if request.kind != "Node" or len(request.resources) != 1:
                return Message("Select one Azure node to open its portal page.", "warning")
            url = portal_url(request.resources[0].raw)
            return OpenUrl(url) if url else Message("This node has no supported Azure VM resource ID.", "warning")
        if request.action != "sign-in":
            return None
        if not cloud_forced(request.settings) and not any(is_aks_node(resource.raw) for resource in request.resources):
            return Message("This node is not on Azure: its providerID is not azure://.", "warning")
        tenant = str(request.settings.get("azure_tenant") or "").strip()
        if tenant and not all(c.isalnum() or c in ".-" for c in tenant):
            return Message("The Azure tenant has characters that are not allowed; use its id or domain.", "warning")
        return Terminal(az_login_argv(tenant, _flag(request.settings.get("azure_device_code", False))))


plugin = AzurePlugin()
