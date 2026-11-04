"""The public plugin API.

A plugin adds information to Klotse without being part of it. The first extension point is **columns**: a plugin
declares extra columns for a kind of resource (Node, Pod, ...) and fills them in `enrich()`, which runs on a worker
thread, so it may use the network. Klotse caches the values per resource and only asks again for new resources,
or after `refresh_s` seconds when the values go stale.

A minimal plugin (a .py file in the plugins folder, or any importable module):

    from klotse.plugins import Column, Plugin

    class NodeZone(Plugin):
        id = "node-zone"
        name = "Node zone"
        description = "Shows the availability zone of every node."
        columns = (Column("zone", "Zone"),)

        def enrich(self, request):
            labels = lambda r: (r.raw.get("metadata") or {}).get("labels") or {}
            return {r.key: {"zone": labels(r).get("topology.kubernetes.io/zone")} for r in request.resources}

    plugin = NodeZone()          # Klotse looks for a module-level `plugin` (an instance) or `register()`

Plugins run with the user's permissions and see everything Klotse shows, so they are off until enabled in
Preferences -> Plugins. `enrich()` must not touch Qt; raise an exception to report a problem (it is shown in the Plugins
dialog and the plugin is retried after a pause).
"""
import re
from dataclasses import dataclass, field

API_VERSION = 1

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
class Setting:
    """A setting the Plugins dialog shows for a plugin. kind is "text" or "bool"; values reach `enrich()` in
    `request.settings` (defaults filled in)."""

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


class Plugin:
    """Base class for plugins. Set the class attributes and override `enrich`."""

    id = ""  # lower case letters, digits, "-" and "_"
    name = ""
    description = ""
    version = ""
    columns = ()  # Column objects
    settings = ()  # Setting objects
    refresh_s = None  # ask again for known resources after this many seconds; None = once per resource

    def enrich(self, request):
        """Return {resource.key: {column key: value}} for the resources in `request`. Runs on a worker thread."""
        return {}
