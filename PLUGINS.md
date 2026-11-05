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
| `Setting(key, label, default, kind="text" or "password" or "note" or "multiline" or "bool", help="")` | A setting the Plugins dialog shows ("multiline" is a text box for lists, one entry per line; "note" shows its label as a large warning box and holds no value; "password" hides what is typed, but it is saved as plain text in the settings file like every other setting); its value reaches `enrich()` and `run()` in `request.settings`. |
| `EnrichRequest` | What `enrich()` receives: `plugin_id`, `kind`, `context` (the kubeconfig context), `resources`, `settings`, `cache_dir` (a folder to keep downloaded data in; create it when needed) and `warn(text)` (report a problem without giving up: the values you return are still used and the text is shown in the Plugins page). |
| `ActionRequest` | What `run()` receives: `plugin_id`, `action` (the key), `kind`, `context`, `resources`, `settings`, plus `log(line)`, `api_client()`, `kubectl` and `cache_dir`. |
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
so the 10 second auto-refresh does not pile up requests. An answer that came with `request.warn(...)` is used, but it
is asked for again after about a minute instead of waiting for `refresh_s`, since what was missing may be fixed by then. Cache anything expensive yourself (a price list, a token): the
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

`klotse.plugins.API_VERSION` is 7: version 1 had columns, version 2 added actions, version 3 added `cache_dir` and
`EnrichRequest.warn`, version 4 added multi-line settings, version 5 added Overview cards, version 6 added cluster
settings, version 7 added password settings. Set `requires_api` to the lowest
version that has what your plugin uses (2 for actions, 3 for `cache_dir` or `warn`, 4 for multi-line settings, 5 for Overview cards, 6 for cluster settings, 7 for password settings); a Klotse that only knows version 1 then lists it as not loadable instead of failing oddly.

## Cluster settings

A plugin that needs something different for each cluster (an account, a profile, a region) declares
`cluster_settings = (Setting("team", "Team", pattern=r"..."),)`. While such a plugin is enabled Klotse shows a **Plugins**
menu with *<plugin>: settings for this cluster…*, enabled when a cluster is in front; the same entry is in the right-click menu of a connected cluster in the left list. The dialog has one text box per
setting; an empty box means "not set for this cluster". The values reach `enrich()`, `overview()` and `run()` in
`request.settings` next to the general settings, for that cluster only (`""` when not set, so the plugin falls back to its
general settings). `Setting.pattern`, if given, is a regular expression a non-empty value must match completely. Saving
makes every open list and Overview ask the plugin again. They are stored in `[plugin_<id>_clusters]` of the settings file.

## The bundled plugin: AWS Plugin

Klotse ships one plugin of its own, **AWS Plugin** (`klotse_plugins/aws`; id `aws`), written against this same API. It is off
until you enable it. For nodes that are EC2 instances (an `aws:///zone/i-...` providerID, which EKS and Karpenter set) it
adds these columns to the Nodes list:

| Column | Where it comes from |
| --- | --- |
| Instance ID, Instance type, Capacity (on-demand / spot / reserved) | the Node's providerID and labels |
| AMI | the EKS node group image label; for every node (also Karpenter's) from AWS when boto3 and credentials work |
| On-demand $/h | AWS's public price list for the region and operating system |
| Price $/h, Price $/month | on-demand nodes: the on-demand price; spot nodes: AWS's current spot price (needs boto3 and credentials, blank otherwise); the month is 730 hours |
| Launched | the instance's launch time from AWS (needs boto3 and credentials) |

and these right-click actions: **Copy instance ID** (several nodes at once), **Open in the EC2 console**, and
**Estimate the cost of the selected nodes** (a text tab with each node and the total per hour and month).

- **No login is needed** for everything except spot prices, launch time and the AMI of nodes without an image label.
  Those nodes' AMI is looked up in EC2 (one DescribeInstances call per region for the nodes not asked about yet); when boto3 is
  not installed the Plugins page says how many nodes have no AMI for that reason, and after a failed lookup (an expired
  AWS login, say) the plugin asks again about a minute later, not half an hour. The AWS lookups need credentials:
  with `boto3` installed they are the usual boto3 ones; without it the plugin signs the two EC2 calls itself and takes
  the credentials from the **AWS CLI** (`aws configure export-credentials`, so IAM Identity Center, assumed-role,
  `credential_process` and plain key profiles all work; AWS CLI v2.9 or newer, which EKS users nearly always have because
  their kubeconfig runs `aws eks get-token`) or from the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` variables. So the
  packaged apps work too, as long as the AWS CLI is installed and signed in.
- **What AWS says about an instance is asked once and kept for good.** An instance's AMI, launch time, zone and whether
  it is spot never change, and an instance id is never reused, so after the first answer the plugin does not ask again,
  not even after a restart and not without credentials: the details are kept in a file per region in the plugin cache
  folder (`instances-<region>.json`). Only spot prices (which move) are fetched again. An instance AWS did not know about
  is not remembered as missing, and a failed call stores nothing, so both are tried again. An entry nobody used for six
  months is dropped when the file is next written (such an instance has long been terminated), which keeps the files
  small. Turning the AWS API setting off also ignores the kept details.
- **Prices** come from the small per-region files that the AWS pricing calculator uses (about 60 KB each, kept for a week in
  the plugin cache folder), not the 480 MB official price list files. That is an undocumented AWS address: if it ever
  changes the plugin says so on the Plugins page and keeps using its last copy.
- **They are list prices for the instance only**: no EBS, data transfer, the EKS cluster fee, savings plans or reserved
  instances, and local-zone nodes are priced like their parent region.
- **A profile for each cluster.** Clusters in other accounts need their own credentials: list them in *AWS profile for each
  cluster*, one per line as `context = profile` (the context is the cluster's name in your kubeconfig), and a cluster that
  is not listed uses *AWS profile (default for all clusters)*. The profile of the cluster a node belongs to is used for the
  EC2 lookups, the cost estimate and the automatic console sign-in. Changing a setting makes the plugin ask again for
  what it showed (the details it already kept for good are not asked again).
- **Profile and account from the menu.** With the plugin enabled and a cluster in front, **Plugins → AWS Plugin: settings
  for this cluster…** sets that cluster's *AWS profile* and, optionally, *AWS account ID* (12 digits). The profile wins
  over the per-cluster list in the plugin's settings and the default profile. The account is normally not needed: for
  "Open in the EC2 console" with several accounts it is taken from the menu, the per-cluster account list, the EKS ARN in
  the context name, and otherwise asked from AWS for the cluster's profile (STS GetCallerIdentity, like
  `aws sts get-caller-identity`; boto3 or the AWS CLI's credentials; kept per profile until Klotse closes).
- **Settings:** *Use the AWS API when credentials are available* (on by default), *AWS profile*, *AWS profile for each
  cluster*, and the console settings below.

### Opening the console with several AWS accounts

"Open in the EC2 console" opens the plain console link, which is right with one account or when you are already signed
in to the right one. If you tick **I use several AWS accounts in the console** the link has to say which account:

- **Which account a cluster is in** is read from its context name when that is the EKS ARN that
  `aws eks update-kubeconfig` creates (it contains the account id). For other context names, list them in *Account for
  each cluster* as `context=123456789012` separated by `;`. If the account cannot be found the action says so instead of
  opening the wrong one.
- **IAM Identity Center:** set *IAM Identity Center start URL* (for example `https://d-1234567890.awsapps.com/start`) and
  *Role name* (the permission set). The link is AWS's documented shortcut link
  (`<start URL>/#/console?account_id=...&role_name=...&destination=...`): it signs in to the cluster's account and opens the
  instance page. Not tested here against a real portal; China and GovCloud portals have other addresses.
- **Switching role:** with only a *Role name*, the instance link is put on the clipboard and AWS's switch-role page is
  opened for that account and role; paste the link after switching (AWS's switch-role page has no destination parameter).

### Signing in automatically

AWS has a documented way for a program to sign a browser in to the console without a login page (the "custom identity
broker" flow). Tick **Sign in to the console automatically** and "Open in the EC2 console" asks AWS for a one-time
sign-in link and opens it, so you are signed in even if you were not:

1. temporary credentials come from the chosen *AWS profile*: used as they are when they are temporary already (IAM
   Identity Center, an assumed role); with several accounts the *Role name* is assumed in the cluster's account (STS
   AssumeRole); with long-term IAM user keys an STS GetFederationToken is used (the console then has what the user may do,
   without IAM and Organizations pages that AWS withholds from federated sessions);
2. the credentials are POSTed to AWS's federation endpoint, which returns a one-time sign-in token;
3. the login link (valid 15 minutes) is opened in the browser.

It needs `boto3` (the sign-in uses STS) and working credentials, is **off by default**, is not used together with an IAM Identity Center start URL
(its own portal signs you in), and the login link is a secret: it goes straight to the browser and is never shown, copied,
logged or kept, and error messages never include it or the credentials. If automatic sign-in fails the normal link opens
with a warning saying why. AWS controls who may do this: the credentials need `sts:AssumeRole` or `sts:GetFederationToken`
as the case may be. Not tested against real AWS here.

## Things to know

- **Plugins are trusted code.** They run with your permissions, in Klotse's process, receive the manifests of the
  resources they are asked about and, for actions, a client for the cluster. Enable only plugins you trust. There is no
  sandbox.
- **Do not touch Qt from `enrich()` or `run()`.** They run on a worker thread.
- **Plugin columns take part in the normal column customization** (order, hide, restore defaults) under the attribute
  name `plugin:<id>:<key>`, so changing a plugin's column keys resets where users put them.
- **Settings** are saved in Klotse's settings file in a `[plugin_<id>]` section.
- Plugin columns and actions appear on the customizable lists only (not on the Overview's small drill-down tables).
