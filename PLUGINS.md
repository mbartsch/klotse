# Writing Klotse plugins

A plugin adds information to Klotse without being part of it. Today a plugin can add **columns** to the resource
lists (Nodes, Pods, ...); the values are looked up in the background, so a plugin may call the network. Everything
a plugin author needs is in `klotse.plugins`:

| Name | What it is |
| --- | --- |
| `Plugin` | Base class. Set `id`, `name`, `description`, `version`, `columns`, `settings`, `refresh_s` and override `enrich()`. |
| `Column(key, title, kinds=("Node",), format=None)` | A column for tables of those kinds (`"Node"`, `"Pod"`, `"Deployment"`, ...). `format` is an optional `value -> str` for display; sorting uses the raw value, so numbers stay numbers. |
| `Setting(key, label, default, kind="text" or "bool", help="")` | A setting the Plugins dialog shows; its value reaches `enrich()` in `request.settings`. |
| `EnrichRequest` | What `enrich()` receives: `plugin_id`, `kind`, `context` (the kubeconfig context), `resources`, `settings`. |
| `ResourceInfo` | One resource: `name`, `namespace`, `raw` (the full manifest, read-only), `key` (`name`, or `namespace/name`), `uid`. |

## A complete plugin

```python
from klotse.plugins import Column, Plugin


class NodeZone(Plugin):
    id = "node-zone"            # lower case letters, digits, "-" and "_"
    name = "Node zone"
    description = "Shows the availability zone of every node."
    columns = (Column("zone", "Zone"),)

    def enrich(self, request):
        # runs on a worker thread: no Qt here; it may use the network
        def zone(resource):
            return ((resource.raw.get("metadata") or {}).get("labels") or {}).get("topology.kubernetes.io/zone")
        return {resource.key: {"zone": zone(resource)} for resource in request.resources}


plugin = NodeZone()             # Klotse looks for a module-level `plugin`, or a `register()` function
```

`enrich()` returns `{resource.key: {column key: value}}`. Values can be strings or numbers (or `None`). A resource
you leave out simply has empty cells. Raising an exception reports a problem in the Plugins dialog; Klotse asks again
after about a minute.

## When Klotse asks

Klotse calls `enrich()` with the resources on screen that it has no values for yet. A resource is asked about once
(and again if it is replaced by one with another uid). Set `refresh_s` (seconds) if the values go stale, for example
prices; the resources are then asked about again after that long. Only one request per plugin and kind is in flight at
a time, so the 10 second auto-refresh does not pile up requests. Cache anything expensive yourself (a price list, a
token): the same plugin object lives for the whole session and `enrich()` may be called from different threads over
time.

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

A plugin that fails to import, has an invalid id, or reuses another plugin's id is listed in the dialog with the reason;
it never stops Klotse or the other plugins.

## Things to know

- **Plugins are trusted code.** They run with your permissions, in Klotse's process, and receive the manifests of the
  resources on screen. Enable only plugins you trust. There is no sandbox.
- **Do not touch Qt from `enrich()`.** It runs on a worker thread.
- **Plugin columns take part in the normal column customization** (order, hide, restore defaults) under the attribute
  name `plugin:<id>:<key>`, so changing a plugin's column keys resets where users put them.
- **Settings** are saved in Klotse's settings file in a `[plugin_<id>]` section.
- Plugin columns appear on the customizable lists only (not on the Overview's small drill-down tables).
