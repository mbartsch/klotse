"""The public plugin API.

A plugin adds information and commands to Klotse without being part of it. It can

* add **columns** to the resource lists: declare `Column`s and fill them in `enrich()`;
* add **actions** to the right-click menu of resources: declare `Action`s and do the work in `run()`.

Both run on a worker thread, so they may use the network, and neither may touch Qt. Klotse caches column values per
resource and only asks again for new resources, or after `refresh_s` seconds when the values go stale.

A minimal plugin (a .py file in the plugins folder, or any importable module):

    from klotse.plugins import Action, Column, Copy, Plugin

    class NodeZone(Plugin):
        id = "node-zone"
        name = "Node zone"
        description = "Shows the availability zone of every node."
        columns = (Column("zone", "Zone"),)
        actions = (Action("copy", "Copy zone"),)

        def zone(self, resource):
            return ((resource.raw.get("metadata") or {}).get("labels") or {}).get("topology.kubernetes.io/zone")

        def enrich(self, request):
            return {r.key: {"zone": self.zone(r)} for r in request.resources}

        def run(self, request):
            return Copy("\\n".join(str(self.zone(r)) for r in request.resources))

    plugin = NodeZone()          # Klotse looks for a module-level `plugin` (an instance) or `register()`

Plugins run with the user's permissions and see everything Klotse shows, so they are off until enabled in
Preferences -> Plugins. A problem (an exception) is shown to the user and, for columns, the plugin is retried after a pause.
"""
import re
from dataclasses import dataclass, field

API_VERSION = 2  # 1: columns. 2: actions in the right-click menus.

_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")


def valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def column_attr(plugin_id, key):
    """The attribute name a plugin column has in tables, layouts and the Columns dialog."""
    return f"plugin:{plugin_id}:{key}"


@dataclass(frozen=True)
class Column:
    """A column a plugin adds to tables of `kinds`.

    key     unique within the plugin; the key `enrich()` returns values under
    title   the column heading
    kinds   resource kinds as the tables name them: "Node", "Pod", "Deployment", ...
    format  optional callable(value) -> str for display only; sorting uses the raw value (numbers stay numbers)
    """

    key: str
    title: str
    kinds: tuple = ("Node",)
    format: object = None


@dataclass(frozen=True)
class Action:
    """An entry a plugin adds to the right-click menu of resources of `kinds`.

    key        unique within the plugin; passed back in `request.action`
    title      the menu text
    kinds      resource kinds: "Node", "Pod", "Deployment", ...
    multi      also offered when several rows are selected (then `request.resources` has all of them)
    confirm    text for a Yes/No question asked before `run()` (default answer No); None = no question
    dangerous  the action changes the cluster or something outside it: it is marked in the menu and always asks first
    """

    key: str
    title: str
    kinds: tuple = ("Node",)
    multi: bool = False
    confirm: str = None
    dangerous: bool = False


@dataclass(frozen=True)
class Setting:
    """A setting the Plugins dialog shows for a plugin. kind is "text" or "bool"; values reach `enrich()` and `run()`
    in `request.settings` (defaults filled in)."""

    key: str
    label: str
    default: object = ""
    kind: str = "text"
    help: str = ""


@dataclass
class ResourceInfo:
    """One resource handed to a plugin: its identity and the full manifest (read it, do not modify it)."""

    name: str
    namespace: str
    raw: dict = field(repr=False)

    @property
    def key(self):
        """The key `enrich()` returns this resource's values under: "name", or "namespace/name" when namespaced."""
        return f"{self.namespace}/{self.name}" if self.namespace else self.name

    @property
    def uid(self):
        return ((self.raw or {}).get("metadata") or {}).get("uid") or ""


@dataclass
class EnrichRequest:
    plugin_id: str
    kind: str
    context: str  # the kubeconfig context the resources come from
    resources: list
    settings: dict


@dataclass
class ActionRequest:
    """What `run()` receives. `log(line)` writes a progress line to Klotse's Logs & activity (safe from the worker
    thread); `api_client()` gives a `kubernetes.client.ApiClient` for this cluster; `kubectl` is the start of a kubectl
    command line for it (explicit --context, and --kubeconfig when one is selected) for use in a `Terminal` result."""

    plugin_id: str
    action: str
    kind: str
    context: str
    resources: list
    settings: dict
    log: object = None
    api_client: object = None
    kubectl: tuple = ()


# ---- what run() may return (one result or a list of them; None for nothing) -----------------------------
@dataclass(frozen=True)
class Message:
    """Tell the user something: the status bar, or a dialog for level "warning" and "error"."""

    text: str
    level: str = "info"


@dataclass(frozen=True)
class Copy:
    """Put text on the clipboard."""

    text: str


@dataclass(frozen=True)
class OpenUrl:
    """Open an http(s) address in the default browser."""

    url: str


@dataclass(frozen=True)
class ShowText:
    """Show text in a read-only tab of the Logs & activity dock."""

    title: str
    text: str


@dataclass(frozen=True)
class Terminal:
    """Run a command in the user's terminal, like Shell and Attach do. argv is a list of strings."""

    argv: tuple


@dataclass(frozen=True)
class Reload:
    """Reload the open lists of these kinds, e.g. after the action changed something."""

    kinds: tuple = ()


class Plugin:
    """Base class for plugins. Set the class attributes and override `enrich` and/or `run`."""

    id = ""  # lower case letters, digits, "-" and "_"
    name = ""
    description = ""
    version = ""
    requires_api = 1  # the lowest API_VERSION that has everything this plugin uses
    columns = ()  # Column objects
    actions = ()  # Action objects
    settings = ()  # Setting objects
    refresh_s = None  # ask again for known resources after this many seconds; None = once per resource

    def enrich(self, request):
        """Return {resource.key: {column key: value}} for the resources in `request`. Runs on a worker thread."""
        return {}

    def applies(self, action, resource):
        """Whether `action` is offered for this resource. Called on the GUI thread each time a menu opens, so it must
        be fast and use only the resource itself (no network, no files)."""
        return True

    def run(self, request):
        """Do the action in `request` (an ActionRequest). Runs on a worker thread. Return results (see above)."""
        return None
