# UI and behavior to preserve

### Workspaces and loading

- Each open cluster has its own `KubeService` and workspace (service factory, no shared client).
- Views load asynchronously; keep loading/error states visible, coalesce repeated refreshes, and ignore late results for disconnected workspaces. Refresh and namespace changes reload the foreground tab.
- Paging: pages of 500, selectors/scope preserved, 30 s request timeout, gzip, short discovery timeout.
- The Details dock is capped at half the window (`_limit_dock`).
- `overview_cluster` reads nodes, pods, events, namespaces, metrics in parallel. Keep overview lists bounded, serialization deferred and `include_raw=False` paths cheap; never serialize all events/pods just to count them.
- Tables keep API order initially; column sizing samples at most 100 rows; filtering debounced 200 ms. Avoid unbounded GUI-thread work.
- Analysis windows (Capacity, Health check, Pod security, Upgrade preflight, Right-sizing, Unused objects, RBAC lookup, Trivy, Search, Change log, My permissions, Export diagnostics, Several clusters) are non-modal, one per workspace (Export diagnostics: per namespace), and come back to the front with their data. Loads go through `ClusterWorkspace._analysis_load`: Refresh disabled until the answer, an older answer dropped by serial, the heading rewritten for the scope of that load. Titles are `{Window} — {alias}` (`ws.display_name`, set by MainWindow); wording, nothing-found line, sorting and Copy come from `ui/analysis_window.py`.
- `ResourceTable.set_rows` remembers and silently restores selection, current row and scroll by uid (else namespace/name); a recreated object with a new uid is not the same selection. Initial loads distinguish progress, empty, filter-empty and failed (Retry, Copy error, View permissions for 401/403 via `ui/load_errors.explain`); a failed refresh keeps visible rows.

### Auto-refresh, watches, and metrics

- Only the foreground tab of a visible cluster reloads: every interval (toolbar Auto-refresh: Off/5/10/30/60 s, default 10 s), when it comes to the front, and on a debounced (800 ms) change watch, never sooner than the interval after the last load. Per workspace, not persisted. The toolbar cannot be hidden (`createPopupMenu` override).
- **Manual refresh** (`ui/refresh_policy.py`, `is_manual`): by default only workloads and nodes reload by themselves (`DEFAULT_AUTO`); every other list loads once on first open, then on Refresh. The row menu has a checkable **Refresh automatically**, saved per list in `[refresh]`.
- Watches: `watch_targets`/`watch_changes` resume after the 20 s server timeout, restart from a fresh list on 410/ERROR, back off on other errors. `ChangeWatcher` is a QThread; `stop()` only sets a flag. Never wait for a watcher on the GUI thread; keep running QThreads referenced.
- The namespace selector shows only for namespaced views. It is searchable and includes a trailing arrow affordance that opens the namespace choices without changing the current selection.
- The namespace selector shows only for namespaced views.
- Preferences > General can save an application font family and point size. Applying it updates the running Qt application and regenerates theme-scaled styles; code and manifest editors retain their monospace font.
- Pod resource columns (CPU/Mem req, lim, %req, %lim) are hidden by default and computed lazily from `raw`.
- Metrics are optional: any failure leaves usage unknown (`-1`, blank) and must never fail a list load.
- Nodes' Pods column: counted once on load, recounted for rows coming into sight after `NODE_PODS_TTL_S` (20 s), all nodes every 60 s while auto-refresh is on; routine reloads keep old numbers.

### Bulk actions

- Extended selection. Right-clicking an unselected row selects only it; with 2+ rows only bulk actions show: Scale, Restart, Terminate/Force terminate, Cordon/Uncordon/Delete/Force delete, Drain N nodes (one kubectl command; overlapping drains blocked). One confirmation, one worker, failures listed at the end; node deletion keeps the UID precondition.
- Restart is a rollout restart (`restartedAt` on the pod template) after Yes/No. Deployments also have **Rollout history / Undo…** (`k8s/rollout.py`, `rollback_deployment` is `@mutating`). StatefulSets/DaemonSets have no history.
- Scale accepts a number or `*2`, `/2` (rounds up), `+1`, `-1`; clamps 0–10000; each gets a scale monitor.
- CronJob menu: Run now…, Suspend/Resume… (Yes/No default No, `@mutating`).

### Tables, ages, and labels

- **Go to** (single row): Go to node X, Go to namespace Y (`goto_requested` -> `go_to`); new tables that want it must connect the signal.
- Age sorting is numeric (`1d` before `8h` before `35m` oldest-first; unknown = `-1`), both directions; CPU/memory stay numeric with separate display. Keep display, edit/sort and search roles distinct.
- Nodes filter across visible values plus label/annotation keys and values; `karpenter.sh/nodepool` shows as `NodePool` after Roles by default.
- Right-click a cell: Copy cell value, Filter by this value (readable quoted equality; blank values cannot be filtered).
- **Columns…** (every list except Container rows, custom resource instance tabs, overview drill-downs: `NOT_CUSTOMIZABLE`): label columns (`label:<key>`, optional `key = Short heading`; heading is display-only and never patches labels), built-in column hide/reorder (Name fixed; saved as `columns/layout`), annotation columns for RBAC kinds (cells cut at 200 chars, tooltip 2,000). Sort column is preserved by attribute; a removed one falls back to unsorted.

### Plugins

- Off until enabled in Preferences > Plugins. Plugins are trusted in-process code; never special-case one in core code, extend the API (and `PLUGINS.md`). `API_VERSION` bumps when the API grows.
- Extension points: table columns (`row.extra["plugin:<id>:<key>"]`), right-click actions (`dangerous` ones are refused read-only and prefixed ⚠), Overview cards, per-cluster settings, variables for a cluster's processes (API 12, below).
- **Variables for commands and terminals** (`Plugin.process_environment(context, settings)`, `PluginManager.process_environment(context)`, `ClusterWorkspace.process_variables()`): asked on the GUI thread when a process starts and passed explicitly as `variables`/`extra` to `k8s/process_env.environment`/`qt_environment`, `open_terminal`, `make_session`, `CommandView`, `PortForwards`, `FileCopies`, `remote_files.list_directory` and helm. A new place that starts kubectl, helm or a terminal for a cluster must pass them. Klotse's own connection (`KubeService.connect(context, variables)`) passes them only to the kubeconfig's exec helper of that context (`_load_kube_config`, in memory, never the process environment or the kubeconfig file); not used for the kubeconfig Sign in terminal. Names and values are checked by the manager; values never go to logs, the change log or a command line (POSIX external terminals get `export` lines in the private session script). The AWS plugin passes a cluster's own profile as `AWS_PROFILE` (never the typed key then), else the typed key, else the default profile (`pass_environment`, on by default); every cloud plugin has `extra_environment` (`NAME=value` lines, `plugins.parse_environment`). The manifests (`klotse_plugins/*.json`) repeat the settings: change both.
- `enrich()`/`run()`/`overview()` run on worker threads and must not touch Qt. Failures are contained: reported, backed off (60 s), shown in Preferences. `PluginHost` applies cached values before the model is filled (`rows_incoming`) so columns do not flicker or shrink; `request.warn` keeps values and retries after 60 s.
- Plugins keep files in `request.cache_dir`. List new bundled modules in `BUNDLED_MODULES` and the spec's hidden imports; the self-test has a `plugins` check. Packaged apps do not include boto3.

### Settings persistence

- All stores share `settings_store.default_store()` (`Klotse/Klotse.ini`, INI, copies the old `Kubeman` file once). Use `LabelSettings.store.fileName()`, never a hard-coded path. Numbered keys (`name.N`), a bare empty key means none, absent means default; avoid `, ; =` in values; do not rely on key order. Invalid saved values fall back to defaults.
- Sync settings before updating in-memory state; surface save errors.
- Custom kubeconfig (`kubeconfig/path`): `MainWindow.new_service()` sets it; every kubectl command gets `--kubeconfig` before `--context` (`terminal.kubectl_base`). Do not rely on `KUBECONFIG`. Changing it asks first and closes all clusters.
- macOS terminal choice (Terminal or iTerm2) is `terminal/macos`.
- Tests must inject a `QSettings` on `tmp_path`; never touch the user's real preferences.

### YAML and secrets

- Details and Edit share `dump_manifest` (`ui/manifest.py`): preserve Unicode, key order, multiline values, trailing newlines; ConfigMap strings must survive dump/load/edit unchanged.
- Secret `raw` masks `data`, `stringData` and the last-applied annotation; encoded values live in `row.secret_data`. Decode is a bounded key picker that works on a copy; Hide re-masks; selecting another resource clears it. Invalid base64 shows an error, non-UTF-8 shows hex. Never log decoded values or put real secrets in fixtures or docs.
- Details hide `managedFields` unless **View > Show managedFields in details** (display copy only).
- Menus: **File** (Apply YAML…, Exit), **Edit** (Undo…Select All, Preferences…), **View** (grouped Navigate / Insights / Operations / Settings; `managedFields` at root), **Plugins** (only if a plugin has cluster settings), **Help**. No Settings menu: everything configurable is in Preferences (Qt moves it to the macOS app menu). Edit acts on the focused text box via `edit_menu.EditActions`; shortcuts use `QKeySequence` standard keys and avoid intercepting editor input.

### Read-only mode

- **View > Read-only mode** (per context, saved): turning on is immediate, off asks Yes/No (default No); toolbar and tab title show 🔒.
- Two layers: `ClusterWorkspace._writable(what)` refuses and explains at the UI (edits, New, delete, scale, restart, bulk, node actions except listing pods, pod terminate, Shell/Attach, dangerous plugin actions; Apply YAML keeps dry run); `KubeService.read_only` makes every `@mutating` method raise `ReadOnlyError`. **A new mutating `KubeService` method must carry `@mutating`; a new UI entry point must call `_writable`.** Looking (lists, logs, Related, dry-run review) stays allowed. It is not a permission boundary (terminals, non-dangerous plugin actions).

### Node actions

- Node menus: pods on node, Cordon/Uncordon, Drain, nsenter shell, Delete node, Force delete. Status combines Ready/NotReady/Unknown with SchedulingDisabled; use `NodeRow.is_ready` for counts, not string equality.
- Delete removes only the Node object (not the cloud VM), with a UID precondition; force uses zero grace and does not remove finalizers.
- Drain uses explicit-context `kubectl drain` streamed to Logs & activity: 10 min timeout, DaemonSet pods skipped, force/emptyDir opt-in, never silently `--disable-eviction` or bypass PDBs. Avoid duplicate concurrent drains of a node; closing kills the local kubectl only (evictions are not rolled back).
- nsenter follows kubectl-node-shell v1.11.0 defaults (privileged pod pinned with `nodeName`, host namespaces, `nsenter --target 1 ... bash -l`, namespace `default`, image `docker.io/library/alpine`); Linux host with bash only.
- The node-pods tab (`node-pods:<node>`, server-side `spec.nodeName`, all namespaces, ignores the namespace filter) carries the node bar; the Nodes list reloads after a node action.

### Pod/container actions and terminals

- Pod menu: Logs, Shell, Attach, Terminate, Force terminate (zero grace, can remove the object before processes stop), **Debug (ephemeral container)…** (`kubectl debug`, cannot be removed afterwards; **Remove debug containers (recreate pod)…** deletes the pod only if a controller owns it), Copy files…, Port forward…, Browse files….
- Double-click a pod opens `pod:<ns>/<name>` (regular, init, ephemeral containers); double-click a container opens its logs; double-click a node opens its pods tab.
- Logs/Shell/Attach target the selected container, context, namespace and pod. Always explicit `--context`. Log options: Wrap, Timestamps, Previous instance (changing a stream option restarts the stream).
- Terminals: Windows `cmd /k` in a new console; macOS Terminal/iTerm2 via osascript with the command as an AppleScript argument (successful sessions close themselves with a targeted detached closer; nonzero exit waits for Enter); Linux tries x-terminal-emulator, gnome-terminal, konsole, xterm. Opening runs in a worker; surface stderr.
- Keep argv structured; where a shell string is required use `shlex.join`.

### Shutdown

- Clusters are not connected at start. `restore_clusters()` reopens those remembered at last exit (plus per-cluster namespace) from `[session]`, written only when the user answers Yes in the exit dialog (`confirm_exit`: Yes remembers, No clears, Cancel aborts; skipped if the window was never shown). `tests/conftest.py` answers it with No.
- File > Exit / OS quit lead to an idempotent `shutdown`: stop workspaces, streams, queued pool work, command views. No Exit button by owner's request.
- `LogStreamer.stop` shuts the socket down; never call `response.close` on the GUI thread. Retain streamers/watchers until their QThreads finish.
- The entry point arms a 3 s daemon `os._exit(0)` fallback; `__main__.leave()` waits up to 1 s for running watchers/streamers and else `os._exit`s (avoids "QThread: Destroyed while thread is still running"). Keep hard-exit behaviour in the entry point and test it in child processes.
