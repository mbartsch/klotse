# Klotse project guide

This file applies to the repository rooted here. It records the current implementation and working conventions; verify details against source when changing them.

## Project and local state

- Klotse is a native Kubernetes desktop manager built with Python and PySide6/Qt Widgets, using the Kubernetes Python client and `kubectl`.
- Package: `klotse`, Python `>=3.10`; the version comes from the git tag (see Packaging), setuptools with a `src/` layout.
- Runtime dependencies: `PySide6-Essentials>=6.6`, `kubernetes>=29`, and `PyYAML>=6`. Development dependencies add `pytest` and `pytest-qt`.
- Work lands on `master` through pull requests (squash-merged on GitHub, `mbartsch/klotse`). Start new work on a branch from an up-to-date `master` and open a PR; do not commit to `master` directly. The `gh` CLI is used for PRs (installed with scoop on the Windows dev machine; it needs `gh auth login` once).
- Preserve uncommitted work in the tree; do not reset or discard it, and do not assume it has been committed or pushed.
- The project is licensed under Apache-2.0 (`LICENSE`, `license` in pyproject.toml, `about.LICENSE`; keep the three in step). The README and About carry the Linux Foundation trademark disclaimer; the Kubernetes name and logo are theirs, so do not use the Kubernetes logo or put the mark in the product name. The logo was redesigned (circle badge, teal/amber, no heptagon) to stay clear of the Kubernetes mark; keep it that way.
- There is no separate web frontend, database, or application server.

## Run and install

From the repository root on macOS/Linux:

```sh
./run.sh
./run.sh --check
./run.sh --install
```

`run.sh` creates `.venv` if needed, installs `requirements.txt` on first run or with `--install`, exports `PYTHONPATH` for `src/`, and launches `python -m klotse`. `--check` checks imports without opening the UI.

Windows equivalents:

```bat
run.bat
run.bat --console
run.bat --check
run.bat --install
```

The default Windows launcher uses `pythonw.exe`; `--console` keeps tracebacks visible. The launchers support their platform's virtualenv layout.

For an existing environment, direct execution is:

```sh
PYTHONPATH=src .venv/bin/python -m klotse
```

Install development requirements when needed:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
```

The package also declares a `klotse` GUI entry point. Use `PYTHONPATH=src` for repository tests unless an editable installation is confirmed; the local virtualenv previously could import dependencies but could not import `klotse` without it.

Cluster access comes from kubeconfig and its existing authentication helpers. `kubectl` must be available on PATH for drain and terminal operations. Shell/debug actions also need suitable cluster permissions and a supported local terminal. Local unit tests do not require a live cluster.

Native Windows/macOS packages are built with PyInstaller (`packaging/`, see the Packaging section below and `packaging/README.md`). A packaged app answers `--version` and `--self-test [FILE]`; the build scripts run the self-test and fail if it does not pass.

## Architecture and file map

| File | Responsibility |
| --- | --- |
| `src/klotse/__main__.py` | QApplication startup, theme, main window, bounded process shutdown. |
| `src/klotse/cli.py` | Command-line actions handled before the GUI starts (no Qt at import): `--version`, `--install-desktop-entry`, `--uninstall-desktop-entry`, `--self-test [FILE]` (checks a packaged build: imports, Qt plugins, logo, dialogs; exit 0 = OK) and `--terminal-session` (the macOS shell runner, reached through the app binary when packaged). |
| `src/klotse/desktop_entry.py` | Linux application-menu entry for pip/pipx installs: `klotse --install-desktop-entry` writes `~/.local/share/applications/klotse.desktop` and the icon (no root), `--uninstall-desktop-entry` removes them. Uses the Desktop Entry quoting rules for `Exec`; `__main__` sets the Wayland desktop file name to `klotse`. |
| `src/klotse/workers.py` | QRunnable worker and Qt result/error signals for blocking service calls. |
| `src/klotse/k8s/client.py` | KubeService, context-specific API clients, pagination, resource loading, mutations, overview aggregation, node-specific pod queries and per-node pod counts, change watches (`watch_targets`/`watch_changes`), optional metrics-server usage, and custom resource listing. |
| `src/klotse/k8s/models.py` | Dataclass rows for namespaces, pods, nodes, and core workloads. Pod/Node rows carry usage fields (`cpu_used`, `mem_used`, `-1` = unknown); Node rows also carry `pods` (`-1` = not counted). |
| `src/klotse/k8s/resources.py` | Generic Row, resource mappers (including CRDs and custom objects), secret masking, and SPECS registry for additional resource views. |
| `src/klotse/k8s/util.py` | Age formatting and Kubernetes model serialization. |
| `src/klotse/ui/main_window.py` | Cluster navigator/workspace shell, visible Exit button, menus, and shared customization settings. Label-column customization is opened from the tabs (see `resource_table.py`). |
| `src/klotse/ui/workspace.py` | One cluster's resource tabs (including per-node pods, per-pod, and per-CRD tabs), namespace scope, detail/activity docks, asynchronous loading, auto-refresh and watches, node pod counts, and single and bulk resource actions. |
| `src/klotse/ui/navigator.py` | Context connection states and grouped view navigation (including the Custom Resources group). |
| `src/klotse/ui/resource_table.py` | Shared table, debounced filtering, sorting, label columns, extended selection with bulk menus, an optional Columns… button (Nodes, Pods, pods-on-node tabs), context menus, per-kind double-click actions, a header-widget slot, and a debounced `visible_changed` signal with `visible_rows()`. |
| `src/klotse/ui/table_model.py` | Display/sort/search roles, numeric age sorting, label lookups, condition/header tooltips, and in-place `refresh_column`. |
| `src/klotse/ui/loading_pane.py` | `LoadingPane`: the large centered "Loading… Ns" / "Load failed: …" message with an animated analog clock (`ClockSpinner`: animates only while visible; it remembers its own angle and adds a capped step (`MAX_STEP_DEG`) each frame instead of deriving the hands from elapsed time, so a hide/show, a relayout or a GUI thread starved by worker threads never resets or leaps the hands; it cannot move faster than the GUI thread gets to paint), shared by the Overview and `ResourceTable`. It stands in for a list that has nothing to show yet (first load or a failed one); reloads with rows on screen only use the table's small status line. |
| `src/klotse/ui/overview.py` | Cluster/workload cards, bounded pod/event drill-down tables, CPU/memory usage bars (hidden without metrics), and a non-blocking large "Loading cluster data… Ns" message with an animated analog clock, centered in the still empty events pane, for the first load (or a failed one with its error; refreshes with data on screen stay quiet). `Workspace.load_view` drives it like a table's `set_loading`/`set_error`. |
| `src/klotse/ui/detail_panel.py` | Read-only YAML and searchable multi-key secret decoding. |
| `src/klotse/ui/manifest.py` | Shared readable, round-trip-safe YAML dumping. |
| `src/klotse/ui/edit_dialog.py` | YAML resource editor using the shared manifest dumper. |
| `src/klotse/ui/log_view.py` | Background log streaming, container selection, search/follow, and stream shutdown. |
| `src/klotse/ui/pod_view.py` | A pod's own tab: regular/init/ephemeral container rows, pod action bar, and container-specific Logs/Shell/Attach. |
| `src/klotse/ui/node_bar.py` | Node action bar (cordon, drain, shell, delete) shown above a node's pods tab. |
| `src/klotse/ui/node_dialogs.py` | Drain options and privileged nsenter debug-shell options. |
| `src/klotse/ui/command_view.py` | QProcess-backed drain output, completion/error reporting, and local process cancellation. |
| `src/klotse/ui/terminal.py` | Explicit-context kubectl argv and platform-specific terminal launching. |
| `src/klotse/ui/about.py` | Help → About Klotse dialog: logo, version, project/GitHub link, author, license, Python/Qt/kubernetes versions, and a Copy details button. Metadata constants (`REPO_URL`, `LICENSE`, `TRADEMARK_NOTE`, ...) live at the top of the module. |
| `src/klotse/ui/branding.py` | Logo from `src/klotse/assets/` (`klotse_logo.png`/`.jpg`, or `logo.png`/`.jpg`; PNG preferred): window/taskbar icon and the About dialog logo (128 px); a non-square image is center-cropped to a square. The shipped `klotse_logo.png` is a 512 px circular badge with a transparent outside, cut from `assets/source/klotse_logo_source.jpg` (not packaged). Without a logo, or with an unreadable one, the app starts normally with no icon. There is deliberately no startup splash screen. Also sets the Windows taskbar app id. |
| `src/klotse/ui/settings_store.py` | The user's settings file (`Klotse/Klotse.ini`); on first start it copies the old `Kubeman/Kubeman.ini` across (the old file is left in place). |
| `src/klotse/ui/terminal_settings.py` | Saved macOS terminal preference and its settings dialog. |
| `src/klotse/ui/terminal_session.py` | Standalone macOS session runner and targeted cleanup after successful commands. |
| `src/klotse/ui/customizations.py` | Persistent per-kind column layout and label columns (readable INI sections), custom headings, and the Columns… dialog. |
| `src/klotse/ui/environment.py` | macOS only: when the app is started from Finder/Dock, extends `PATH` (Homebrew and other usual directories plus the login shell's `PATH`, with a 3 s timeout) so `kubectl` and kubeconfig auth helpers are found. |
| `src/klotse/ui/edit_menu.py` | Edit menu actions (undo/redo/cut/copy/paste/select all) for the focused text box or table. |
| `src/klotse/ui/preferences.py` | Custom kubeconfig setting and the Preferences dialog (kubeconfig picker, open config folder). |
| `src/klotse/ui/scale_monitor.py` | Polling scale progress, readiness/failure evaluation, timeouts, and activity display. |
| `src/klotse/ui/watcher.py` | `ChangeWatcher` QThread: runs `KubeService.watch_changes` and emits `changed`; `stop()` only sets a flag and never blocks the GUI thread. |
| `src/klotse/ui/namespace_dialog.py` | Namespace creation form. |
| `src/klotse/ui/theme.py` | Application palette and Qt stylesheet. |
| `scripts/benchmark_large_cluster.py` | Offline synthetic pod-mapping/table-load benchmark. |
| `packaging/` | Windows and macOS packages (PyInstaller `klotse.spec`, `windows/build.ps1` + `klotse.iss` for Inno Setup, `macos/build.sh` + entitlements) and `README.md` with the build, signing and release steps. `.github/workflows/package.yml` builds them in CI and drafts a release on a `v*` tag. |

Core resource tables cover Nodes, Namespaces, Pods, Deployments, ReplicaSets, and StatefulSets. The SPECS registry adds DaemonSets, Jobs, CronJobs, ConfigMaps, Secrets, Services, Ingresses, NetworkPolicies, PVCs, PVs, StorageClasses, CustomResourceDefinitions, and Events. Secrets and Events have no Edit action. Custom resource instances (`cr:<plural>.<group>` tabs) are read-only.

## UI and behavior to preserve

### Workspaces and loading

- Each open cluster has an independent KubeService and workspace. The shell uses a service factory, not one shared client for all contexts.
- Open views load asynchronously. Refresh and namespace changes reload the foreground tab (plus the namespace list for Refresh); other tabs reload when they come to the front (see auto-refresh below).
- Keep loading/error states visible and prevent stale callbacks from updating disconnected workspaces. Coalesce repeated refreshes rather than starting duplicate loads for a view.
- The shared API pagination helper requests pages of 500, preserves selectors/scope, and uses a 30-second request timeout. Connections request gzip and use a short initial discovery timeout.
- Preserve bounded overview lists, deferred serialization where used, and the inexpensive `include_raw=False` summary paths. Do not eagerly serialize all event or pod manifests just to count them.
- Tables initially preserve API order. Column sizing samples at most 100 rows; filtering is debounced by 200 ms. Avoid introducing unbounded GUI-thread sizing or rendering work.

### Auto-refresh, watches, and metrics

- Only the foreground tab of a visible, non-minimized cluster reloads: every `AUTO_REFRESH_MS` (default 10 s; toolbar toggle and 5/10/30/60 s interval), when a tab or cluster comes to the front, and when a change watch fires (debounced 800 ms). Hidden tabs, hidden clusters, views still loading, and clusters not yet connected are skipped. With the toggle off nothing reloads automatically; the Refresh button still works. These settings are per workspace and not persisted.
- Watches: `KubeService.watch_targets(key, namespace)` lists what to watch for a view; `watch_changes` starts from the current list version, resumes after each 20 s server-side timeout, restarts from a fresh list after 410/ERROR, and backs off on other errors. `ChangeWatcher` (QThread) runs it; `stop()` only sets a flag. Never wait for a watcher on the GUI thread, and keep running QThreads referenced until they finish. Views without targets (node-pods, pod tabs, custom resources) are polled only. Services without `watch_targets` are simply polled.
- The namespace selector is shown only for namespaced views; it is hidden on the cluster Overview, Nodes, Namespaces, PVs, StorageClasses, CRDs, pod tabs, and node-pods tabs, which ignore it.
- Pod resources: the Pods table has CPU/Mem req, lim, `%req` and `%lim` columns, **hidden by default** (`customizations.DEFAULT_HIDDEN`, used until a layout is saved for the kind; Restore defaults re-hides them). They need no extra API call or mapping work: `PodRow.resources` parses requests/limits lazily from `raw` (cores/MiB summed over regular containers, 0 = unset, blank; init containers ignored) only when a column is read, and the percentages are properties (used ÷ request/limit, `-1` = blank when there is no metric or no request/limit; can exceed 100%). Rows without a manifest (`include_raw=False`) show blanks. Overview drill-down tables use `POD_COLUMNS_BASIC` without them.
- Usage is attached by `list_pods`, `list_node_pods` (one cluster-wide pod-metrics call, skipped for empty nodes) and `read_pod` (a single-pod metrics get); every failure leaves usage unknown.
- Metrics are optional: `node_usage`/`pod_usage` read `metrics.k8s.io` and return nothing when metrics-server is missing or not permitted. Unknown usage is `-1` and displays blank; the Overview usage bars appear only when usage exists. Metrics calls must never fail a list load.
- Nodes' Pods column: every node is counted once on first load (`all_node_pod_counts`, one pass over unfinished pods, nothing serialized); rows coming into sight are recounted when older than `NODE_PODS_TTL_S` (20 s) with `node_pod_counts` (one small query per node); all nodes are recounted every `NODE_PODS_REFRESH_MS` (60 s) while auto-refresh is on and the cluster is showing. Routine reloads keep the previous numbers.
- CRDs: Custom Resources → Definitions lists CRDs; double-click or "Browse instances" opens a read-only instance tab through the CustomObjects API.

### Bulk actions

- Tables use extended selection (Ctrl/Shift/Ctrl-A). Right-clicking an unselected row selects only that row; with two or more rows selected the menu shows only bulk actions, per kind: Scale (Deployments, ReplicaSets, StatefulSets), Terminate/Force terminate (Pods), Cordon/Uncordon/Delete/Force delete (Nodes). Edit and the other single-row actions are not offered.
- A bulk action confirms once for the whole group and runs in one worker; one failing row does not stop the others, and failures are listed at the end. Node deletion asks Yes/No (default No) and still sends each node's UID precondition.
- Bulk Scale asks for text: a number, or an operation relative to each workload's own count (`*2`, `/2` rounds up, `+1`, `-1`; `x`/`×` also multiply). Results clamp to 0–10000. Each scaled workload gets its own scale monitor.

### Tables, ages, and labels

- Age sorting compares numeric durations, while display retains compact strings. Oldest-first examples: `1d`, `8h`, `35m`. Both sort directions work. Missing/unrecognized ages use a sort value of `-1`.
- Numeric CPU/memory values remain numeric for sorting, with separate display formatting.
- Nodes filter across visible values plus label and annotation keys/values. Cached metadata search text is rebuilt when rows change.
- Nodes show `karpenter.sh/nodepool` as `NodePool` immediately after Roles by default. A missing label displays an empty cell.
- Label columns are customized from a **Columns…** button next to the filter on the Nodes, Pods, and pods-on-node tabs (there is no global Customizations menu). It has separate ordered label lists for Nodes and Pods. Pod label columns follow Name. Changes update existing workspaces, overview pod tables, and node-specific pod tabs, and apply to subsequently opened workspaces/tabs.
- Label-column model attributes use `label:<original-key>`. A short heading changes presentation only; lookups and tooltips retain the original Kubernetes key.
- The dialog accepts one key per line, optionally `key = Short heading`, for example:

```text
karpenter.sh/nodepool = Pool
app.kubernetes.io/name = App
example.com/team = Team
```

- Every resource list (Deployments, DaemonSets, Services, Events, Namespaces, …) has the same Columns… button and dialog, with its own saved label columns and layout per kind; only Container rows, custom-resource instance tabs, and the overview drill-down tables are fixed (`NOT_CUSTOMIZABLE`, `customizable=False`). Kinds are free-form strings in settings.
- The same dialog has a **Built-in columns** list: untick to hide, Move up/down to reorder. The first column (Name) is fixed and always shown. Saved in `columns/layout` (JSON: `{kind: {order, hidden}}`) via `LabelSettings.layout`/`set_layout`; applied by `ResourceTable.set_label_columns(keys, headings, layout)`. Label columns still follow Roles (Nodes) or Name (Pods), or Name if Roles is hidden. Restore defaults resets both.
- Available label keys can be searched, added, and given an optional Short heading. Users can edit/reorder the list, clear it to remove columns, or restore defaults.
- Preserve active sort columns by attribute when inserting/removing/renaming label columns; a removed sort column falls back to unsorted order.

### Settings persistence

- `LabelSettings`, `TerminalSettings` and `KubeconfigSettings` share `settings_store.default_store()`: `QSettings(IniFormat, UserScope, "Klotse", "Klotse")`. The project was renamed from Kubeman to Klotse; the GitHub repository is still `mbartsch/klotse` until it is renamed there.
- Column preferences are plain INI, one section per resource kind and one entry per line: `[columns.Pod]` with `order.0=name`, `order.1=namespace`, `hidden.0=cpu_req`, `labels.0=app.kubernetes.io/name`, `labels_name.0=App` (the optional heading of `labels.0`, paired by number), `labels.1=example.com/team`. Lists are numbered keys (read in numeric order, so `labels.10` follows `labels.9`); a bare empty key (`hidden=`, `labels=`) means none and an absent key means the default; only kinds the user changed are written; headings may contain any single-line text. Older files are rewritten in this format when the app starts (`LabelSettings.__init__`): the JSON values (`columns/labels`, `columns/layout`) the previous single-line `a | b | c` lists and `key -> heading` values. QSettings decides the order of keys and sections in the file; do not rely on it. Keys avoid `,`, `;` and `=` in values because QSettings quotes those.
- Invalid saved configuration falls back to defaults. Default Node labels contain `karpenter.sh/nodepool`; Pod labels are empty.
- Heading changes are display-only and must never patch Kubernetes labels.
- Sync settings before updating in-memory preferences/emitting changes. Surface save errors to the user.
- On macOS, Settings → Terminal… selects Terminal (default) or iTerm2 for all pod shell/attach and node shell actions. `terminal/macos` is saved in the same QSettings store; invalid values fall back to Terminal. Open and subsequently created workspaces share the preference. The menu is macOS-only; iTerm2 must be installed separately.
- The physical settings path is platform-specific; use `LabelSettings.store.fileName()` rather than hardcoding it.
- Tests must inject a QSettings instance backed by `tmp_path`; do not overwrite the user's actual preferences.

### YAML and secrets

- Details and Edit share `dump_manifest`. Preserve Unicode, dictionary order, multiline values, and trailing newlines. ConfigMap strings must survive a dump/load/edit cycle without changing their contents.
- Secret `raw` manifests mask `data`, `stringData`, and the last-applied-configuration annotation. Encoded `data` values are kept separately on `row.secret_data`.
- Decode values is a bounded, scrollable, case-insensitive key picker tested with 200 entries. Checkboxes preserve selection across filters; Decode selected reveals only those keys in Details (YAML).
- Details hide `metadata.managedFields` by default. **View → Show managedFields in details** (`MainWindow.managed_fields_action`, unchecked at start, not persisted) toggles it for all open and later-opened workspaces via `DetailPanel.set_show_managed_fields`, redrawing the shown resource (decoded secret keys and scroll position are kept). Only the display copy is filtered; `row.raw` and the Edit dialog still contain managedFields.
- Menus (in order): **File** (Exit), **Edit** (Undo, Redo, Cut, Copy, Paste, Select All, Preferences…), **View** (Show managedFields in details), plus **Settings → Terminal…** on macOS, then **Help** (About Klotse). Edit acts on the focused text box, or the last one used while a menu is open (`edit_menu.EditActions`); in tables, Copy puts the selected rows on the clipboard as tab-separated text. The actions stay enabled while the menu is closed so shortcuts work (text boxes still handle their own keys first).
- **Edit → Preferences…** (`preferences.PreferencesDialog`): select a custom kubeconfig (Browse… / Use default) and **Open config folder**, which opens the folder of `LabelSettings.store.fileName()` (Klotse's settings file) with `QDesktopServices.openUrl`, creating it if nothing was ever saved.
- Custom kubeconfig: saved as `kubeconfig/path` (`KubeconfigSettings`; a saved file that no longer exists is ignored). `MainWindow.new_service()` sets `service.kubeconfig`; `KubeService` passes it as `config_file` to the Python client, and every kubectl command (shell, attach, node shell, drain) gets an explicit `--kubeconfig <path>` before `--context` (`terminal.kubectl_base`). Do not rely on the `KUBECONFIG` environment variable: the Python client reads it at import time and macOS terminals start fresh shells. Changing it asks first, closes all open clusters and reloads the context list.
- Decode a copied display manifest; do not modify the masked row/raw object. Hide decoded values masks them again. Selecting another resource clears decoded state.
- Invalid base64 displays an error string; non-UTF-8 bytes display hexadecimal. Do not log decoded values or put real secret material in fixtures, settings, or documentation.

### Node actions

- Node menus provide pod listing, Cordon/Uncordon according to current state, Drain, nsenter shell, Delete node, and Force delete node.
- Status combines `Ready`, `NotReady`, or `Unknown` with `SchedulingDisabled` and active non-Ready conditions. Unknown condition states are retained. The Status tooltip includes full condition statuses, reasons, and messages.
- A Ready cordoned node still contributes to the overview's ready-node count; use `NodeRow.is_ready`, not equality of the entire status string.
- Cordon/uncordon patches `spec.unschedulable`. Deletion requires confirmation and a selected-node UID precondition so a replacement node with the same name is not deleted.
- Node deletion removes the Kubernetes Node object; it does not terminate a cloud VM. A running kubelet can register again. Force uses zero grace period and does not remove finalizers.
- Drain uses explicit-context `kubectl drain` and streams output in Logs & activity. Default timeout is 10 minutes; DaemonSet pods are skipped by default. Force/unmanaged-pod and emptyDir-data options are opt-in. Do not silently add `--disable-eviction` or bypass PodDisruptionBudgets.
- Avoid duplicate concurrent drains for the same node. Stopping/closing a drain kills the local kubectl process; already admitted evictions are not rolled back and the node can remain cordoned.
- nsenter follows the Linux defaults from the installed kubectl-node-shell v1.11.0 script (verified with stubbed kubectl): `kubectl run` creates a uniquely named privileged pod pinned with `nodeName`, sharing host PID/IPC/network namespaces and tolerating CriticalAddonsOnly/NoExecute taints. It runs `nsenter --target 1 --mount --uts --ipc --net --pid bash -l`, defaults to namespace `default` and image `docker.io/library/alpine`, and requires a Linux host with bash. Namespace/image are configurable; the image needs nsenter. `--restart=Never --rm -it` attaches and removes the pod when the session exits; abrupt terminal termination or connection loss may require manual pod cleanup. The plugin does not need to be installed. Source: https://github.com/kvaps/kubectl-node-shell/blob/master/kubectl-node_shell. Optional plugin modes (Windows HostProcess, X-mode, PVC mounts, quota probing) are not implemented.
- List pods on this node (or double-clicking a node) opens/reuses `node-pods:<node>` with a server-side `spec.nodeName=<node>` selector across all namespaces. The workspace namespace filter intentionally does not restrict these tabs. They retain normal pod actions and label customization.
- That tab has an action bar (`node_bar.py`): Cordon/Uncordon, Drain, Shell (nsenter), Delete node, Force delete, using the same confirmations as the Nodes menu. After a node action the Nodes list is reloaded even in the background so the bar follows the node's state; if the node disappears the bar's actions are disabled.

### Pod/container actions and terminals

- Pod menus support Logs, Shell, Attach, normal termination, and explicit Force terminate. Confirm mutations; force deletion uses zero grace period and can remove the API object before host processes stop. Finalizers still apply.
- Double-clicking a pod opens its own tab (`pod:<ns>/<name>`) listing its containers, including regular, init, and ephemeral containers, with state, readiness, restart count, and image; double-clicking a container opens its logs. The tab refreshes like any foreground tab and disables its actions if the pod disappears. Double-clicking a node opens its pods tab, which carries the node actions.
- Logs, Shell, and Attach must target the selected container, cluster context, namespace, and pod. Container-specific log tabs preserve the initial container selection.
- Commands use explicit `--context`; do not rely on the shell's active kubeconfig context.
- Windows terminals use `cmd /k` in a new console. macOS uses Terminal by default or the saved iTerm2 choice via osascript; both pass the quoted command as an AppleScript argument. Linux searches x-terminal-emulator, gnome-terminal, konsole, then xterm. All terminal opening runs in a worker; macOS automation errors are surfaced with their stderr.
- Successful macOS commands exit the local shell and close their session using a detached closer, even if the terminal profile keeps exited sessions open. iTerm2 cleanup matches the created window ID and unique session ID; moving the session into another window leaves it open. Terminal cleanup matches window ID and TTY, waits for the local process to finish, and only closes a window that still has one tab; adding tabs to that window keeps it open to preserve them (Terminal does not support AppleScript closing individual tabs). Nonzero exits display the status and wait for Enter before closing. The runner uses an absolute script/interpreter path and does not import Qt; no background pool worker is held for the session lifetime.
- Keep argv structured. Where a shell command is required, quote with `shlex.join`; on macOS pass it as an AppleScript argument rather than interpolating it into script source.

### Shutdown

- A visible top-right Exit button, File/OS quit action, and standard quit shortcut all lead to shutdown.
- MainWindow shutdown is idempotent: stop workspaces/streams, clear queued global-pool work, and stop command views. Disconnected workspaces must ignore late results.
- LogStreamer.stop shuts down the socket to unblock reading; do not call response.close on the GUI thread because its lock can freeze the UI. Retain streamers until QThreads finish.
- The application entry point arms a daemon timer with a three-second `os._exit(0)` fallback so a stuck API worker/credential helper cannot hold Python open during Qt teardown. Native application Quit is covered too.
- Keep hard-exit behavior confined to the executable entry point. Test it in child processes so it cannot kill the test runner.

## Tests and verification

Run the full offline suite from the repository root:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Useful focused commands:

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_customizations.py tests/test_age_sort.py tests/test_node_filter.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_containers.py tests/test_node_actions.py tests/test_exit.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_manifest.py tests/test_secret_decode.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_bulk.py tests/test_auto_refresh.py tests/test_watch.py tests/test_metrics.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_node_pods.py tests/test_node_bar.py tests/test_pod_tab.py tests/test_inline_customizations.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_performance.py::test_large_table_initial_load_and_filter
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_large_cluster.py --pods 10000
git diff --check
```

- `test_app.py` covers the app shell, navigation, views, resource mapping/actions, logs, scaling, CRDs, and numeric formatting. New feature tests are split by the filenames above (bulk actions, auto-refresh/namespace selector, watches, metrics, node pod counts, node action bar, pod tabs, inline label-column customization).
- Use fake services, synthetic Kubernetes objects, monkeypatched terminals, and local child processes. Local validation must not mutate a real cluster merely to exercise UI code.
- Wait for asynchronous callbacks/loading to finish before tearing down fixtures. Keep owning windows alive; QWidget/QObject ownership and pending events matter with PySide6.
- Qt enum comparisons should use the class enum, e.g. `QDialog.Accepted`, not `dialog.Accepted`. The latter caused an actual DrainDialog/NodeShellDialog traceback and was fixed.
- For popup-menu tests, a QTimer can select/close the menu inside its nested event loop; simply patching QMenu.exec did not reliably prevent a blocked popup in this environment.
- Keep display-role, edit/sort-role, and search-role behavior distinct. Cover both age sort directions and label aliases without changing label lookup keys.
- pytest-qt pitfalls seen here: `qtbot.waitUntil` callbacks must return a real bool (wrap lists in `bool(...)`); `QAction` has no `isVisibleTo` (use `isVisible()`); stub `ws.load_view` before `open_view` when a test feeds rows itself, otherwise a real worker starts.
- On Windows, multi-line Python or heredocs passed through the Bash tool get mangled; write scripts to files (or use the file tools) instead.
- Automated checks use the offscreen Qt platform and mocked infrastructure. They do not prove live RBAC, drain success, nsenter permissions/image execution, cloud termination, or end-to-end terminal integration.

### Validation snapshot

At the last check the full offline suite reported **287 passed** (counts are snapshots; rerun after edits; `tests/test_packaging_support.py` covers the CLI flags, the frozen-app terminal command and the macOS `PATH` helper; `tests/test_desktop_entry.py` covers the Linux menu entry). The five overview tests in `tests/test_performance.py` that once failed on stale return shapes were updated in PR #3 and pass; current `_overview_pods()` returns `(counts, lists)` and `_overview_events()` returns `(events, warning_count, warning_rows)`, so keep the drill-down data when changing those tests.

Known, unresolved: after all tests pass the pytest process can still exit non-zero (139 segfault, or 127) when a test that builds a `ClusterWorkspace` is followed by a test that runs a background worker. It also happens on `master` from before these features; it is intermittent and did not go away with deferred worker cleanup, parenting `QSettings`, or draining the thread pool. Single test files usually exit 0 (not reliably; `test_bulk.py` and `test_performance.py` have crashed on their own), while the full run exited 127 at the last check. Do not report a passing count as proof of a clean exit, and check the process exit status separately.

Live checks against a real cluster were read-only (listing, a watch, metrics when present). Watch events arriving, real metrics-server numbers, CRD instances, and the cordon/drain/delete actions from the new bars and tabs have not been exercised against a live cluster.

## Packaging

- Windows and macOS packages are built with PyInstaller from `packaging/klotse.spec` (see `packaging/README.md`). Build in the isolated `.venv-build`; output goes to the git-ignored `dist/` and `build/`.
- Keep the packaged app working when changing shared code. In a frozen app `sys.frozen` is set, `sys.executable` is the app binary (not Python) and modules have no loose `.py` files next to them: never start a helper as `sys.executable some_script.py`, and ship any file read at runtime by listing it in the spec's `datas` (the files directly in `src/klotse/assets/` are included automatically; `assets/source/` is not).
- `Klotse --self-test [FILE]` is the packaged-app smoke test: add a check there when the app starts depending on something that could go missing from a bundle (a plugin, a data file, a hidden import). The build scripts run it and fail on a non-zero exit.
- The version comes from the git tag through setuptools-scm (`[tool.setuptools_scm]` in pyproject.toml): tag `v0.2.0` gives `0.2.0`, commits after a tag give `0.2.1.devN`. It is written to the generated, git-ignored `src/klotse/_version.py`, which `klotse.__version__` reads (falling back to the installed metadata, then `0+unknown`); About, `--version`, the Windows version resource, the macOS `Info.plist` and the installer all use `klotse.__version__`. There is no version number to edit in a file. The build scripts run `python -m setuptools_scm --force-write-version-files`, and every CI checkout uses `fetch-depth: 0` so the tags are present. Windows file versions and the plist need plain numbers, so `klotse.spec` and `build.ps1` use the numeric prefix (`0.2.1`). Only a tagged commit has a clean version; the PyPI `publish` job refuses `.dev` builds. `MANIFEST.in` keeps `.github`, `packaging`, `scripts` and `assets/source` out of the sdist (setuptools-scm would otherwise add every tracked file). Without any tag setuptools-scm gives an odd `1.devN+unknown`, so make sure a tag exists.
- The Windows `AppId` GUID in `packaging/windows/klotse.iss` must never change once a release exists (it lets installers upgrade and uninstall).
- Verified so far, on a Windows machine: the one-folder build and its self-test, a headless launch of the packaged exe, and the Inno Setup installer (silent per-user install, installed app self-test, clean uninstall). Also verified in GitHub Actions: the Windows build and installer, the macOS arm64 and x86_64 builds (self-test inside `Klotse.app`, ad-hoc signature, DMG created), the Linux pipx smoke jobs (Python 3.10/3.12/3.13, headless and Xvfb self-test, desktop entry), and, with the `v0.1.0` tag, the release job (draft release with the four packages and `SHA256SUMS.txt`, which the owner then published). Not verified: the installer's interactive wizard, all-users mode and upgrades; which parts of the macOS app the owner exercised on a real Mac (the owner tested the 0.1.0 release on an Apple Silicon Mac, an M3 Pro running macOS 26.7.1; which features - the Terminal/iTerm2 prompt and shell sessions, the Finder `PATH` fix, Gatekeeper, opening the DMG - was not recorded, so ask before claiming any one of them; the Intel x86_64 build has not been run on an Intel Mac); a downloaded release installed on a clean machine; and the tag check and `.dev` refusal in the PyPI `publish` job on a clean `v0.1.1` (v0.1.0's upload failed only because that version already existed). Say so when reporting packaging status; do not call them tested.
- Linux is served by the Python package (`pipx install klotse`, or `pipx install git+https://github.com/mbartsch/klotse`), not by a native package: AppImage/.deb/.rpm are the next step if needed; Flatpak/Snap are poor fits because the sandbox blocks `kubectl`, cloud auth helpers and the host terminal. `pyproject.toml` carries the PyPI metadata (keep `classifiers` valid for PyPI, `readme` links absolute, and only `src/klotse/assets/*.png|jpg` shipped: `assets/source/` stays out of the wheel). `.github/workflows/pypi.yml` builds the sdist and wheel, installs the wheel with pipx on Python 3.10/3.12/3.13, runs the self-test headless and under Xvfb, checks the desktop entry, and publishes with trusted publishing only for a `v*` tag through the `pypi` environment. Version 0.1.0 is on PyPI and on the GitHub Releases page (published 2026-11-04; the PyPI upload came from a manual run, before the version came from the tag). Publishing is irreversible and a version can never be reused, so do not trigger it without the owner's explicit go-ahead.
- Packages are unsigned; see `packaging/README.md` for what signing and notarization still need (a Windows Authenticode certificate and an Apple Developer ID).

## Change conventions

- Keep changes scoped to the requested behavior and preserve existing uncommitted work. Update this guide when behavior or the file map changes.
- Put shared table behavior in ResourceTable/TableModel, shared formatting in manifest.py, and service mutations in KubeService rather than duplicating UI-specific mechanisms.
- Keep blocking network/terminal-opening work off the GUI thread. Use existing Worker or QProcess patterns and respect workspace shutdown guards.
- Maintain Windows, macOS, and Linux launch/terminal paths when editing shared code; distinguish mocked coverage from actual OS verification.
- Keep runtime dependency declarations in pyproject.toml and requirements.txt consistent when changing dependencies.
- Do not commit credentials, kubeconfig data, real secret values, or per-user preferences.
- Report the checks actually run and any failures; do not label a targeted pass as a passing full suite.
- Documentation changes do not authorize deployment, cloud-instance termination, pushing branches, or live Kubernetes mutations.
