"""Azure (AKS) plugin: signs in to Azure for an AKS cluster's nodes, in a terminal.

An AKS kubeconfig usually runs `kubelogin` (Microsoft Entra ID), which reuses the account `az` is signed in with. Klotse does
not run the sign-in itself (a browser or a code prompt is needed): the action opens a terminal with `az login`, and the user
connects again. MFA is answered there."""
from klotse.plugins import Action, Message, Plugin, Setting, Terminal
from . import clusters

HELP_AZURE = "Set this when the cluster's nodes do not report their cloud in the providerID (a local emulator, a custom node pool). auto uses the providerID check; azure accepts this cluster's nodes without it."
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
    name = "Azure (AKS) Plugin"
    version = "0.1"
    requires_api = 11
    description = "Signs in to Azure (az login) for the nodes of an AKS cluster, in a terminal."
    actions = (
        Action("sign-in", "Sign in to Azure for this cluster (az login)…", kinds=("Node",)),
    )
    cluster_settings = (
        Setting("cloud", "Cloud of this cluster", "auto", pattern=r"auto|azure", help=HELP_AZURE),
    )
    settings = (
        Setting("azure_tenant", "Azure tenant (optional)", "",
                help="A tenant id or domain for az login, when the account belongs to more than one tenant. Empty uses the default."),
        Setting("azure_device_code", "Sign in with a device code", False, kind="bool",
                help="Shows a code to enter in a browser, for a terminal without a browser. Off opens the browser."),
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

    def run(self, request):
        if request.action != "sign-in":
            return None
        if not cloud_forced(request.settings) and not any(is_aks_node(resource.raw) for resource in request.resources):
            return Message("This node is not on Azure: its providerID is not azure://.", "warning")
        tenant = str(request.settings.get("azure_tenant") or "").strip()
        if tenant and not all(c.isalnum() or c in ".-" for c in tenant):
            return Message("The Azure tenant has characters that are not allowed; use its id or domain.", "warning")
        return Terminal(az_login_argv(tenant, _flag(request.settings.get("azure_device_code", False))))


plugin = AzurePlugin()
