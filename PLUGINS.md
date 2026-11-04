# Writing Klotse plugins

A plugin adds information and commands to Klotse without being part of it. A plugin can:

- add **columns** to the resource lists (Nodes, Pods, ...), filled in in the background so it may call the network;
- add **actions** to the right-click menu of resources (and of several selected rows), such as "Copy instance ID" or
  "Open in the AWS console".

Everything a plugin author needs is in `klotse.plugins`:

| Name | What it is |
| --- | --- |
| `Plugin` | Base class. Set `id`, `name`, `description`, `version`, `requires_api`, `columns`, `actions`, `settings`, `refresh_s` and override `enrich()` (columns), `run()` (actions) and optionally `applies()`. |
| `Column(key, title, kinds=("Node",), format=None)` | A column for tables of those kinds (`"Node"`, `"Pod"`, `"Deployment"`, ...). `format` is an optional `value -> str` for display; sorting uses the raw value, so numbers stay numbers. |
| `Action(key, title, kinds=("Node",), multi=False, confirm=None, dangerous=False)` | A right-click entry. `multi`: also offered when several rows are selected. `confirm`: a Yes/No question asked first. `dangerous`: it changes things; marked with ⚠ and always asks first. |
| `Setting(key, label, default, kind="text" or "bool", help="")` | A setting the Plugins dialog shows; its value reaches `enrich()` and `run()` in `request.settings`. |
| `EnrichRequest` | What `enrich()` receives: `plugin_id`, `kind`, `context` (the kubeconfig context), `resources`, `settings`. |
| `ActionRequest` | What `run()` receives: `plugin_id`, `action` (the key), `kind`, `context`, `resources`, `settings`, plus `log(line)`, `api_client()` and `kubectl`. |
| `ResourceInfo` | One resource: `name`, `namespace`, `raw` (the full manifest, read-only), `key` (`name`, or `namespace/name`), `uid`. |
| `Message`, `Copy`, `OpenUrl`, `ShowText`, `Terminal`, `Reload` | What `run()` may return (see below). |

## A complete plugin

```python
from klotse.plugins import Action, Column, Copy, Plugin


class NodeZone(Plugin):
    id = "node-zone"            # lower case letters, digits, "-" and "_"
    name = "Node zone"
    description = "Shows the availability zone of every node."
    columns = (Column("zone", "Zone"),)
    actions = (Action("copy", "Copy zone", multi=True),)

    def zone(self, resource):
        labels = (resource.raw.get("metadata") or {}).get("labels") or {}
        return labels.get("topology.kubernetes.io/zone")

    def enrich(self, request):
        # runs on a worker thread: no Qt here; it may use the network
        return {resource.key: {"zone": self.zone(resource)} for resource in request.resources}

    def run(self, request):
        return Copy("\n".join(str(self.zone(resource)) for resource in request.resources))


plugin = NodeZone()             # Klotse looks for a module-level `plugin`, or a `register()` function
```

## Columns

`enrich()` returns `{resource.key: {column key: value}}`. Values can be strings or numbers (or `None`). A resource you
leave out simply has empty cells. Raising an exception reports a problem in the Plugins dialog; Klotse asks again after
about a minute.

Klotse calls `enrich()` with the resources on screen that it has no values for yet. A resource is asked about once (and
again if it is replaced by one with another uid). Set `refresh_s` (seconds) if the values go stale, for example prices;
the resources are then asked about again after that long. Only one request per plugin and kind is in flight at a time,
so the 10 second auto-refresh does not pile up requests. Cache anything expensive yourself (a price list, a token): the
same plugin object lives for the whole session and may be called from different threads over time.

## Actions

An `Action` becomes a right-click entry for tables of its `kinds`. With one action a plugin's entry is flat; with
several, they are in a submenu named after the plugin. With several rows selected only actions with `multi=True` are
offered and `request.resources` has all of them.

- `applies(action, resource)` (optional) decides per resource whether an entry is shown. It runs on the GUI thread every
  time a menu opens: keep it fast and look only at the resource itself. For several rows the entry is shown only if it
  applies to every one.
- `run(request)` does the work on a worker thread. It may call the network, use `request.api_client()` (a
  `kubernetes.client.ApiClient` for that cluster, already authenticated like Klotse itself) and write progress with
  `request.log(line)` (safe from the worker thread; lines go to the **Plugins** tab of Logs & activity). Every run also
  writes one line there: which plugin, which action, on which resources.
- `confirm="..."` asks that Yes/No question first (default answer No). `dangerous=True` marks the entry with ⚠ and always
  asks, with a standard question naming the plugin and the resources if you gave no `confirm` text. Use it for anything
  that changes the cluster or something outside it. It is a label that triggers the question, not a sandbox.
- `run()` returns one result, a list of them, or `None`:

| Result | What Klotse does |
| --- | --- |
| `Message(text, level="info")` | status bar; a dialog for `"warning"` and `"error"` |
| `Copy(text)` | puts the text on the clipboard |
| `OpenUrl(url)` | opens the address in the browser (only `http` and `https`) |
| `ShowText(title, text)` | a read-only tab in Logs & activity |
| `Terminal(argv)` | runs the command in the user's terminal, like Shell and Attach; `request.kubectl` is the start of a kubectl command line for this cluster (explicit `--context`, and `--kubeconfig` when one is selected) |
| `Reload(kinds=())` | reloads the open lists of those kinds (all open lists if empty), e.g. after changing something |

An exception from `run()` is shown to the user and recorded in the Plugins dialog; it never stops Klotse.

## Installing a plugin

Plugins are found in three places, and none runs until you enable it on the **Plugins** page of Preferences (Edit → Preferences… on Windows and Linux, the application menu's Settings… on macOS):

1. **The plugins folder**: `.py` files, or folders with an `__init__.py`, in the `plugins` folder next to Klotse's
   settings file. The Plugins dialog has **Open plugins folder** and **Reload plugins**. This works in the packaged
   apps too. A file or folder whose name starts with `_` or `.` is ignored.
2. **A pip package** that declares an entry point in the `klotse.plugins` group, installed in the same environment as
   Klotse (`pipx inject klotse my-klotse-plugin`):

   ```toml
   [project.entry-points."klotse.plugins"]
   node-zone = "my_klotse_plugin:plugin"      # a Plugin instance, a Plugin subclass, or a function returning one
   ```

3. **Bundled with Klotse**, for plugins the project ships itself. They are loaded like any other plugin and are just as
   disabled until enabled.

A plugin that fails to import, has an invalid id, reuses another plugin's id, or needs a newer API than this Klotse
provides (`requires_api`) is listed in the dialog with the reason; it never stops Klotse or the other plugins.

## API versions

`klotse.plugins.API_VERSION` is 2: version 1 had columns, version 2 added actions. Set `requires_api = 2` if your
plugin uses actions; a Klotse that only knows version 1 then lists it as not loadable instead of failing oddly.

## Things to know

- **Plugins are trusted code.** They run with your permissions, in Klotse's process, receive the manifests of the
  resources they are asked about and, for actions, a client for the cluster. Enable only plugins you trust. There is no
  sandbox.
- **Do not touch Qt from `enrich()` or `run()`.** They run on a worker thread.
- **Plugin columns take part in the normal column customization** (order, hide, restore defaults) under the attribute
  name `plugin:<id>:<key>`, so changing a plugin's column keys resets where users put them.
- **Settings** are saved in Klotse's settings file in a `[plugin_<id>]` section.
- Plugin columns and actions appear on the customizable lists only (not on the Overview's small drill-down tables).
