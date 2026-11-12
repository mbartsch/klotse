# Klotse project guide

Applies to the repository rooted here. It records the current implementation and working conventions; verify details against source when changing them. The previous long version (per-feature verification notes, measurements) is in git history.

## Project and local state

- Klotse is a native Kubernetes desktop manager: Python + PySide6/Qt Widgets, the Kubernetes Python client and `kubectl`. No web frontend, database or server.
- Package `klotse`, Python `>=3.10`, setuptools `src/` layout. The version is one line in `src/klotse/_version.py` and changes only in a release PR (see Packaging).
- Runtime deps: `PySide6-Essentials>=6.6`, `PySide6-WebEngine>=6.6` (embedded terminal; about 300 MB, accepted by the owner), `kubernetes>=29`, `PyYAML>=6`, `pywinpty>=2.0.1` on Windows. Dev deps add `pytest`, `pytest-qt`. Keep `pyproject.toml` and `requirements.txt` consistent.
- Work lands on `master` through squash-merged PRs (`mbartsch/klotse`). Branch from an up-to-date `master`; never commit to `master`. Use `gh` for PRs.
- Preserve uncommitted work in the tree; do not reset or discard it, and do not assume it is committed or pushed.
- License Apache-2.0 (`LICENSE`, pyproject `license`, `about.LICENSE`: keep in step). README and About carry the Linux Foundation trademark disclaimer. Do not use the Kubernetes logo or put the mark in the product name; the logo is a circle badge, teal/amber, no heptagon: keep it that way.

## Run and install

```sh
./run.sh [--check|--install]      # macOS/Linux: creates .venv, installs requirements.txt, runs python -m klotse
run.bat [--console|--check|--install]   # Windows; default uses pythonw.exe, --console shows tracebacks
PYTHONPATH=src .venv/bin/python -m klotse   # existing environment
.venv/bin/python -m pip install -r requirements-dev.txt
```

Use `PYTHONPATH=src` for tests unless an editable install is confirmed. Cluster access comes from kubeconfig and its auth helpers; `kubectl` must be on PATH for drain and terminals. Unit tests need no live cluster. Packaged apps answer `--version` and `--self-test [FILE]`.

## Architecture and file map

Core: `__main__.py` (startup, theme, bounded shutdown), `cli.py` (pre-GUI actions: `--version`, `--install/--uninstall-desktop-entry`, `--self-test`, `--terminal-session`; no Qt at import), `_display.py` (git-checkout display version `X.Y.Z+g<commit>`), `desktop_entry.py` (Linux menu entry), `workers.py` (QRunnable worker + signals), `k8s/client.py` (`KubeService`: per-context clients, pagination, loads, `@mutating` writes, overview, watches, metrics, custom resources), `k8s/models.py` (row dataclasses; usage fields `-1` = unknown), `k8s/resources.py` (generic Row, mappers, secret masking, `SPECS`, `CUSTOM_COLUMNS`), `k8s/util.py`.

UI shell: `ui/main_window.py` (navigator/workspace shell, menus, shared settings), `ui/workspace.py` (`ClusterWorkspace`: one cluster's tabs, namespace scope, docks, loading, refresh/watch, actions), `ui/navigator.py`, `ui/resource_table.py` + `ui/table_model.py` (shared table), `ui/detail_panel.py`, `ui/loading_pane.py`, `ui/overview.py`, `ui/theme.py`, `ui/branding.py`, `ui/about.py`, `ui/settings_store.py`, `ui/preferences.py`, `ui/customizations.py`, `ui/environment.py` (macOS PATH), `ui/edit_menu.py`, `ui/status_tone.py`.

Feature modules (pure logic under `k8s/`, windows under `ui/`; each has its own `tests/test_<name>.py`):

| Feature | Files | Key points |
| --- | --- | --- |
| Cluster groups | `ui/cluster_groups.py`, `group_suggest.py` | Group paths for disconnected contexts (`[groups]`); suggestions by name prefix or regex; connected clusters list flat. |
| Pinned namespaces, saved filters/forwards | `namespace_pins.py`, `saved_filters.py`, `saved_forwards.py` | Per-cluster INI sections; a save error raises `OSError` and the caller keeps old state. |
| Filter expressions | `ui/filter_expr.py` | Pure parser: `Restarts > 3 and kind = pod*`, units taken from the column (`cpu*` cores, `mem*` MiB, `age` seconds), labels as `label.x`, manifest paths (`spec.replicas >= 2`, exact keys, numeric list indices, scalar leaves only, quote quantities); blank values match nothing, even `!=`; bad expression filters nothing and shows a note. |
| Compare | `k8s/compare.py`, `ui/compare_dialog.py` | Unified YAML diff of two rows; Secrets compare masked; makes no API call. |
| Change log | `k8s/audit.py`, `ui/audit_log.py`, `audit_dialog.py` | Append-only `audit.log`; records identity only (never bodies/Secret data). Hooks: the `@mutating` decorator, `apply_documents`, `ClusterWorkspace.record_change` (non-service changes: new ones must call it). |
| Copy as new | `k8s/clone.py` | `clone_manifest` for Pod/workload/Job/CronJob; strips server fields, Pods lose labels, CronJob created suspended. |
| Capacity, Health, Right-sizing, Pod security, Unused, Upgrade preflight | `k8s/capacity.py`, `health.py`, `rightsizing.py`, `pod_security.py`, `unused.py`, `preflight.py` + matching `ui/*_dialog.py` | View-menu analysis windows: one non-modal window per workspace, closed by `shutdown`, read-only (allowed on read-only clusters). Pure logic over manifests; advice, not verdicts. Unused refuses to list if a reference source is unreadable; Secrets are read as metadata only. |
| RBAC lookup | `k8s/rbac_lookup.py`, `ui/rbac_dialog.py` | Who can / what can a subject do, from RBAC objects only (not webhooks/cloud IAM). |
| Usage history | `k8s/metrics_history.py`, `ui/line_chart.py`, `metrics_dialog.py`, `k8s/prometheus.py`, `ui/metrics_sources.py` | metrics-server sampled every 15 s while open (in memory), or Prometheus/VictoriaMetrics through the API server service proxy. QPainter chart. |
| Schema help | `k8s/schema.py`, `ui/schema_help.py` | YAML editor docs panel, completion and unknown-field status from the cluster's own OpenAPI v3. |
| Review changes | `ui/review_dialog.py` | Save goes through a server `dryRun=All` plus diff (`KubeService.dry_run_save`, `install_dry_run`; a write that cannot get the parameter raises rather than being sent). Not a change: allowed read-only, not logged. Secrets are not reviewed. |
| Command palette | `ui/palette.py`, `palette_dialog.py` | Ctrl+K fuzzy list built each time from navigator views, namespaces, contexts and enabled menu actions. |
| Diagnostics export | `k8s/diagnostics.py`, `ui/diagnostics_dialog.py` | Zip of one namespace; Secrets never read; env values redacted by default; logs, args, labels not cleaned (README says so). Own `QThread`, cancellable. |
| Gateway API, Karpenter, CRD groups | `k8s/gateway.py`, workspace `_find_*` | Found by discovery, not assumed; Gateway kinds are editable as YAML (`replace_custom`/`delete_custom`, `@mutating`); other custom resources are read-only. A new table kind needs its entry in the action dict in `ClusterWorkspace.__init__`. |
| Helm | `k8s/helm.py`, `ui/helm_dialog.py` | Releases from storage Secrets (labels only for the list); values hidden until requested, secret-looking keys stay hidden; Uninstall runs `helm` with explicit `--kube-context`. |
| Trivy | `k8s/trivy.py`, `ui/trivy_dialog.py` | Table-based report lists; exposed-secret matches are never shown. |
| Search | `k8s/search.py`, `ui/search_dialog.py` | Metadata-only search across kinds, bounded (`HIT_LIMIT`, `MAX_PAGES`), cancellable. |
| Multi-pod logs | `k8s/selectors.py`, `ui/multi_log.py` | Stern-like tab; at most 40 streams; stops with the tab/cluster. |
| Several clusters | `ui/multi_cluster.py` | Merged list of one kind from ticked open clusters; reads only on request; actions limited to compare, show, logs, restart. |
| Related / Events / formatted views | `k8s/related.py`, `ui/related_view.py`, `events_view.py`, `workload_view.py`, `rbac_view.py` | Pure HTML renderers that escape every cluster string; a Secret is never fetched for Related. |
| Port forward, file copy, file browser | `ui/port_forward.py`, `file_copy.py`, `k8s/remote_files.py`, `ui/file_browser.py` | `QProcess` kubectl with explicit `--context`/`--kubeconfig` (`terminal.kubectl_base`); stopped on tab/cluster close; the remote path is its own argv entry after `--`. |
| Embedded terminal | `ui/embedded_terminal.py`, `pty_session.py`, `terminal_settings.py`, `assets/xterm/` | xterm.js in a `QWebEngineView` in its own top-level window (never a child of the cluster window, which rebuilds its native handle), pty program side without Qt; falls back to the external terminal. Default off. Output reaches the page as base64, never HTML. |
| Terminals/drain | `ui/terminal.py`, `terminal_session.py`, `command_view.py`, `node_dialogs.py`, `k8s/drain_plan.py` | See "Pod/container actions". |
| Pod/node views | `ui/pod_view.py`, `node_bar.py`, `node_overview.py`, `action_bar.py` | Pod tab, node action bar (one Actions menu) with request/usage overview from already-loaded data. |
| Editors | `ui/edit_dialog.py`, `workload_editor.py`+`workload_model.py`+`workload_widgets.py`, `network_editor.py`+`network_model.py`, `data_editor.py`, `role_editor.py`, `binding_editor.py`, `service_account_editor.py`, `apply_model.py`+`apply_dialog.py` | Forms change only the fields they show and return untouched manifests unchanged; "Edit as YAML…" and back loop in `_edit_flow`. Secrets are edited only through `DataEditDialog` (decode per key on request, merge patch of changed keys). RBAC edits warn and ask Yes/No (default No) for default roles/bindings, `cluster-admin`, system groups, aggregate labels. Apply YAML is server-side apply under field manager `klotse`, never forcing. |
| Permissions | `ui/permissions_dialog.py`, `k8s/permissions.py` | My permissions + Can I?; menu hints show only a definite False as "(not allowed for you)", never hide or disable. |
| Big-cluster mode | `ui/big_clusters.py`, `k8s/timing.py` | Auto-detected from 50 nodes / 1,500 pods, per-cluster `auto/on/off`; larger pages, side-by-side and plain-JSON reads, Table API node pod counts, paged Pods list from 5,000 pods (`PodPager`, idle auto-load, `append_rows` instead of reset), refresh interval counted from the end of the last load. Timing log (Preferences, off by default) writes names, counts and seconds only. Owner's 18,000-pod cluster numbers were accepted; no further optimisation planned. |
| Session/state | `ui/cluster_session.py`, `read_only.py` | Reconnect list written only by the exit dialog; read-only contexts. |
| AWS plugin | `src/klotse_plugins/aws/` | Bundled plugin (public API only): node columns, EC2 console links, cost cards. Cluster data is untrusted: use `safe.py` (`valid_region`, `require_https`, `parse_xml`) for any request code; never log or show the federation login URL; credentials objects never print secrets. |
| Plugins | `plugins/api.py`, `manager.py`, `host.py`, `ui/plugins_panel.py`, `cluster_settings_dialog.py`, `plugin_actions.py` | See "Plugins". |
| Packaging | `packaging/`, `.github/workflows/package.yml` | PyInstaller; see Packaging. |

Resource kinds: Nodes, Namespaces, Pods, Deployments, ReplicaSets, StatefulSets are core; `SPECS` adds DaemonSets, Jobs, CronJobs, ConfigMaps, Secrets, Services, Ingresses, NetworkPolicies, PVCs, PVs, StorageClasses, CRDs, Events, HPAs, PDBs, Resource Quotas, Limit Ranges, Priority Classes, webhook configurations, Endpoints, EndpointSlices, Leases and the Access Control group (Service Accounts, Roles, Bindings, ClusterRoles, ClusterRoleBindings). Events, Endpoints, EndpointSlices and Leases have no Edit. Delete (`KubeService.delete_resource`, `DELETABLE_KINDS`) asks Yes/No (default No) and sends the UID precondition. Custom resource instance tabs are read-only. To offer annotation columns for a kind add it to `customizations.ANNOTATION_KINDS`; for custom resource columns add to `CUSTOM_COLUMNS`.

## UI and behavior to preserve

### Workspaces and loading

- Each open cluster has its own `KubeService` and workspace (service factory, no shared client).
- Views load asynchronously; keep loading/error states visible, coalesce repeated refreshes, and ignore late results for disconnected workspaces. Refresh and namespace changes reload the foreground tab.
- Paging: pages of 500, selectors/scope preserved, 30 s request timeout, gzip, short discovery timeout.
- The Details dock is capped at half the window (`_limit_dock`).
- `overview_cluster` reads nodes, pods, events, namespaces, metrics in parallel. Keep overview lists bounded, serialization deferred and `include_raw=False` paths cheap; never serialize all events/pods just to count them.
- Tables keep API order initially; column sizing samples at most 100 rows; filtering debounced 200 ms. Avoid unbounded GUI-thread work.
- `ResourceTable.set_rows` remembers and silently restores selection, current row and scroll by uid (else namespace/name); a recreated object with a new uid is not the same selection. Initial loads distinguish progress, empty, filter-empty and failed (Retry, Copy error, View permissions for 401/403 via `ui/load_errors.explain`); a failed refresh keeps visible rows.

### Auto-refresh, watches, and metrics

- Only the foreground tab of a visible cluster reloads: every interval (toolbar Auto-refresh: Off/5/10/30/60 s, default 10 s), when it comes to the front, and on a debounced (800 ms) change watch, never sooner than the interval after the last load. Per workspace, not persisted. The toolbar cannot be hidden (`createPopupMenu` override).
- **Manual refresh** (`ui/refresh_policy.py`, `is_manual`): by default only workloads and nodes reload by themselves (`DEFAULT_AUTO`); every other list loads once on first open, then on Refresh. The row menu has a checkable **Refresh automatically**, saved per list in `[refresh]`.
- Watches: `watch_targets`/`watch_changes` resume after the 20 s server timeout, restart from a fresh list on 410/ERROR, back off on other errors. `ChangeWatcher` is a QThread; `stop()` only sets a flag. Never wait for a watcher on the GUI thread; keep running QThreads referenced.
- The namespace selector shows only for namespaced views.
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
- Extension points: table columns (`row.extra["plugin:<id>:<key>"]`), right-click actions (`dangerous` ones are refused read-only and prefixed ⚠), Overview cards, per-cluster settings.
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

## Tests and verification

```sh
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q          # full suite, ~10 min: run in background
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_<area>.py
git diff --check
```

- Use fake services, synthetic objects, monkeypatched terminals and local child processes; never mutate a real cluster to exercise UI code. Use the real row classes (a stand-in with extra attributes hides bugs).
- Wait for async work to finish before teardown; keep owning windows alive. Compare Qt enums via the class (`QDialog.Accepted`). `qtbot.waitUntil` callbacks must return a real bool. Stub `ws.load_view` before `open_view` when a test feeds rows itself. Tests that need a connected cluster call `open_active(win)` (from `test_app.py`). Popup-menu tests need a QTimer to close the menu. The offscreen render has no text.
- On Windows, write multi-line Python to files; the Bash tool mangles heredocs, quotes and backslashes. Files are CRLF in the working tree: keep each file's line endings, never use `git -c core.autocrlf=false add`. A failing patch script can empty its target: compute the new text before opening for writing, prefer Edit, check `git diff --numstat`.
- Known: after all tests pass the pytest process can still exit non-zero (139 or 127) when a `ClusterWorkspace` test is followed by a background-worker test. Report the pass count and the exit status separately.
- Report only checks actually run. Offline tests and docker-desktop service-layer scripts do not prove live RBAC, drain, nsenter, terminals, other clusters or how a window looks on a display. `LIVE_TESTING.md` lists what is verified and pending. Features were mostly never seen on a real display, and several (Trivy, Prometheus, preflight on removed APIs, Several clusters with two clusters, Helm uninstall, embedded terminal on Linux/macOS, AWS plugin against AWS, packaged builds on clean machines) are unverified: say so.
- `scripts/benchmark_large_cluster.py` is an offline synthetic benchmark. README captures: `PYTHONPATH=src python scripts/capture_screenshots.py` (offscreen, invented data).

## Packaging

- PyInstaller from `packaging/klotse.spec` in the isolated `.venv-build`; output in git-ignored `dist/` and `build/`. See `packaging/README.md`.
- In a frozen app `sys.executable` is the app binary and there are no loose `.py` files: never run `sys.executable some_script.py`; ship runtime-read files via the spec's `datas` (files directly in `src/klotse/assets/` are automatic, `assets/source/` is not).
- `--self-test` is the packaged smoke test; add a check when the app starts depending on something that could be missing from a bundle. QtWebEngine is required there in packaged apps only.
- **Releases:** the version lives in `src/klotse/_version.py` and changes only in a release PR (branch `release/vX.Y.Z`, title `Release vX.Y.Z`, only the version file and `CHANGELOG.md`) defined by `scripts/release.py` and `.github/workflows/release.yml`. Merging tags the merge commit; Package and PyPI workflows run on `v*` tags (PyPI waits for the `pypi` environment approval). Build scripts write a git-ignored `_build_info.py` so the app reports `X.Y.Z+g<commit>` off a tag. A `RELEASE_TOKEN` secret lets release PRs start their own checks. **Never tag or publish by hand or without the owner's explicit go-ahead; publishing is irreversible.**
- The Windows `AppId` GUID in `klotse.iss` must never change.
- Linux is served by the Python package (pipx), not a native package. Keep PyPI `classifiers` valid, `readme` links absolute, and `assets/source/` out of the wheel. Packages are unsigned.
- Verified: Windows one-folder build, installer (silent per-user), macOS arm64/x86_64 CI builds, Linux pipx smoke jobs, the v0.1.0 release job. Not verified: interactive installer wizard, all-users mode, upgrades, which features the owner exercised on a Mac, the Intel build, a clean-machine install, the release workflow end to end.

## Session hand-off (updated 2026-11-12)

- Released: `v0.1.0`, `v0.2.0`. Everything merged since is unreleased; release only on the owner's explicit go-ahead. The file map above covers the merged features.
- Open issues (each needs `enhancement` and an `effort:` label): #61 (done, owner to close), #63, #64, #99, #115, #131. Skip #99, #63, #115. `ROADMAP.md` lists everything.
- Asked for, not done: an external URL in the usage chart's **Source…** and a "show all" view.
- Untracked `windows.zip` and the owner's `timing*.log` files are not Claude's: never stage them; add files by name.
- Owner's workflow: one branch and one squash-merged PR per feature, no stacked PRs; commits end with the `Co-Authored-By` line and PR bodies with the "Generated with Claude Code" line from the session reminder; every feature PR updates this file and `ROADMAP.md` and states what was and was not verified. **Standing instruction: merge each PR as soon as every check passed** (checks exist, none pending or failed, PR head equals local HEAD, all tested in the merge command); re-run checks that failed because GitHub itself failed (`gh run rerun <id> --failed`), never merge over them.
- CodeQL flags `startswith`/prefix tests on strings as URL sanitisation: compare exactly instead.
- A background permission classifier may refuse actions without explanation: do not work around it; finish what you can and report what was blocked.
- Real-API checks use the owner's `docker-desktop` cluster through scripts in the session scratchpad; clean up what you create. Installs of metrics-server, Gateway API CRDs and Traefik there were allowed.

## Security checks

- `.github/workflows/security.yml` runs bandit (`bandit -r src scripts -ll`; fix or justify with `# nosec Bxxx - reason`), pip-audit, zizmor and dependency review; `codeql.yml` runs CodeQL; Dependabot updates pip and Actions weekly. `SECURITY.md` is the reporting policy.
- Workflows: pin every action to a commit SHA with the tag in a comment, set `permissions` explicitly, use `persist-credentials: false` on read-only checkouts, and pass untrusted text (PR titles, branch names) through `env:`, never inline in `run:`.
- Data from a cluster is untrusted: validate it before it builds an address or a command.

## Change conventions

- Keep changes scoped to the request and preserve existing uncommitted work. Update this guide when behavior or the file map changes, concisely.
- Shared table behavior goes in ResourceTable/TableModel, shared formatting in `manifest.py`, service mutations in `KubeService`.
- Keep blocking network and terminal-opening work off the GUI thread (existing Worker or QProcess patterns, respect shutdown guards).
- Keep Windows, macOS and Linux launch/terminal paths working; distinguish mocked coverage from actual OS verification.
- Do not commit credentials, kubeconfig data, real secret values or per-user preferences.
- Documentation changes do not authorize deployment, cloud-instance termination, pushing branches or live Kubernetes mutations.
- Pending, not merged at the time of writing: main menu grouping (#232), actionable load states (#234), manifest filter paths (#215; described above).

## CI and platform notes (2026-11-12)

- `.github/workflows/tests.yml`: `pytest` runs the suite on `ubuntu-latest` in six shards for every non-draft PR and pushes to `master`; `pytest-release` runs the same six shards on `windows-latest` and `macos-26` (arm64) only for non-draft PRs from `release/*` branches. Files are picked with `sorted(tests/test_*.py)[SHARD::SHARD_COUNT]`; `pytest-release` writes `SHARD_COUNT` out, so keep it equal to its shard list. Intel macOS runs only in the manual `full-suite` job (runner, Qt platform and Python version are inputs). A required status check or ruleset must name the current check names (`pytest (linux, shard N)`, ...): workflow YAML alone cannot make a check mandatory. Recheck all CI after each workflow change; do not merge while a required check is pending or failing.
- The macOS segfault in `test_window_populates_tables` was most likely `LoadingPane`'s Copy error button being added to its row twice (fixed). Do not reintroduce `QWidget.grab()` in offscreen loading-clock tests. Linux full runs under WSL/Xvfb can segfault in Qt rendering: use focused tests or avoid native-rendering assertions.
- Workflow action refs must be full 40-character SHAs (`actions/setup-python@v7` is `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0`).
- Open decision: whether full macOS tests belong on every PR, nightly or manual only (macOS minutes cost about 10x Linux); the release-only shard and a manual OS selector are the planned next steps.
- Review fixes under #218: external POSIX terminal scripts run through `/bin/sh`; Windows external sessions pass argv in a private JSON file to `terminal_session` (`--terminal-session Windows FILE`, also for packaged GUI builds), never through `cmd.exe`; keep shell metacharacters as argument data. The Windows bounded shutdown uses `TerminateProcess` when Qt threads stay blocked. Workspaces must parent even unopened and newly discovered views, and overviews must own unused cards/headings, so disconnecting releases them. Test tips: size toolbar tests from the size hint, show parented tables through their workspace tabs, release shortcut modifiers after synthetic `keyClick`. Real macOS/Linux terminals and a fresh packaged Windows build still need platform checks.
- PR hygiene: when closing a PR without merging, comment why (superseded, stale, duplicate, abandoned) and link the replacement.
