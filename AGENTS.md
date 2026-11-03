# Kubeman project guide

This file applies to the repository rooted here. It records the current implementation and working conventions; verify details against source when changing them.

## Project and local state

- Kubeman is a native Kubernetes desktop manager built with Python and PySide6/Qt Widgets, using the Kubernetes Python client and `kubectl`.
- Package: `kubeman`, version `0.1.0`, Python `>=3.10`, setuptools with a `src/` layout.
- Runtime dependencies: `PySide6-Essentials>=6.6`, `kubernetes>=29`, and `PyYAML>=6`. Development dependencies add `pytest` and `pytest-qt`.
- This checkout is on `mbartsch/resource-management`, renamed from `mbartsch/configmap`. This is session context, not a required name for future branches.
- The current resource-management work includes modified tracked files and new untracked modules/tests. Preserve existing work; do not reset, discard, or assume it has been committed or pushed.
- There is no separate web frontend, database, or application server.

## Run and install

From the repository root on macOS/Linux:

```sh
./run.sh
./run.sh --check
./run.sh --install
```

`run.sh` creates `.venv` if needed, installs `requirements.txt` on first run or with `--install`, exports `PYTHONPATH` for `src/`, and launches `python -m kubeman`. `--check` checks imports without opening the UI.

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
PYTHONPATH=src .venv/bin/python -m kubeman
```

Install development requirements when needed:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
```

The package also declares a `kubeman` GUI entry point. Use `PYTHONPATH=src` for repository tests unless an editable installation is confirmed; the local virtualenv previously could import dependencies but could not import `kubeman` without it.

Cluster access comes from kubeconfig and its existing authentication helpers. `kubectl` must be available on PATH for drain and terminal operations. Shell/debug actions also need suitable cluster permissions and a supported local terminal. Local unit tests do not require a live cluster.

## Architecture and file map

| File | Responsibility |
| --- | --- |
| `src/kubeman/__main__.py` | QApplication startup, theme, main window, bounded process shutdown. |
| `src/kubeman/workers.py` | QRunnable worker and Qt result/error signals for blocking service calls. |
| `src/kubeman/k8s/client.py` | KubeService, context-specific API clients, pagination, resource loading, mutations, overview aggregation, and node-specific pod queries. |
| `src/kubeman/k8s/models.py` | Dataclass rows for namespaces, pods, nodes, and core workloads. |
| `src/kubeman/k8s/resources.py` | Generic Row, resource mappers, secret masking, and SPECS registry for additional resource views. |
| `src/kubeman/k8s/util.py` | Age formatting and Kubernetes model serialization. |
| `src/kubeman/ui/main_window.py` | Cluster navigator/workspace shell, visible Exit button, menus, and shared customization settings. |
| `src/kubeman/ui/workspace.py` | One cluster's resource tabs, namespace scope, detail/activity docks, asynchronous loading, and resource actions. |
| `src/kubeman/ui/navigator.py` | Context connection states and grouped view navigation. |
| `src/kubeman/ui/resource_table.py` | Shared table, debounced filtering, sorting, label columns, selection, context menus, and pod double-click actions. |
| `src/kubeman/ui/table_model.py` | Display/sort/search roles, numeric age sorting, label lookups, and condition/header tooltips. |
| `src/kubeman/ui/overview.py` | Cluster/workload cards and bounded pod/event drill-down tables. |
| `src/kubeman/ui/detail_panel.py` | Read-only YAML and searchable multi-key secret decoding. |
| `src/kubeman/ui/manifest.py` | Shared readable, round-trip-safe YAML dumping. |
| `src/kubeman/ui/edit_dialog.py` | YAML resource editor using the shared manifest dumper. |
| `src/kubeman/ui/log_view.py` | Background log streaming, container selection, search/follow, and stream shutdown. |
| `src/kubeman/ui/container_dialog.py` | Regular/init/ephemeral container list and container-specific Logs/Shell/Attach actions. |
| `src/kubeman/ui/node_dialogs.py` | Drain options and privileged nsenter debug-shell options. |
| `src/kubeman/ui/command_view.py` | QProcess-backed drain output, completion/error reporting, and local process cancellation. |
| `src/kubeman/ui/terminal.py` | Explicit-context kubectl argv and platform-specific terminal launching. |
| `src/kubeman/ui/customizations.py` | Persistent Node/Pod label-column lists, custom headings, and settings dialog. |
| `src/kubeman/ui/scale_monitor.py` | Polling scale progress, readiness/failure evaluation, timeouts, and activity display. |
| `src/kubeman/ui/watcher.py` | `ChangeWatcher` QThread: runs `KubeService.watch_changes` and emits `changed`; `stop()` only sets a flag and never blocks the GUI thread. |
| `src/kubeman/ui/namespace_dialog.py` | Namespace creation form. |
| `src/kubeman/ui/theme.py` | Application palette and Qt stylesheet. |
| `scripts/benchmark_large_cluster.py` | Offline synthetic pod-mapping/table-load benchmark. |

Auto-refresh and watches (`workspace.py`): only the foreground tab of a visible cluster reloads, every `AUTO_REFRESH_MS` (toolbar toggle and interval) and whenever a change watch fires (debounced 800 ms). Background tabs reload when they come to the front; Refresh and namespace changes also touch only the foreground tab. Views without `watch_targets` (node-pods, custom resources) are polled only. Metrics (`node_usage`/`pod_usage`) and CRD browsing (`list_custom_objects`) degrade to blank/empty when metrics-server or the API is unavailable; usage fields are `-1` when unknown. Double-click: Pod opens containers, Namespace scopes the toolbar and opens Pods, CRD opens its instances.

Core resource tables cover Nodes, Namespaces, Pods, Deployments, ReplicaSets, and StatefulSets. The SPECS registry adds DaemonSets, Jobs, CronJobs, ConfigMaps, Secrets, Services, Ingresses, NetworkPolicies, PVCs, PVs, StorageClasses, and Events. Secrets and Events have no Edit action.

## UI and behavior to preserve

### Workspaces and loading

- Each open cluster has an independent KubeService and workspace. The shell uses a service factory, not one shared client for all contexts.
- Open views load asynchronously. Namespace changes reload namespace-scoped open views; Refresh reloads open tabs plus the namespace list.
- Keep loading/error states visible and prevent stale callbacks from updating disconnected workspaces. Coalesce repeated refreshes rather than starting duplicate loads for a view.
- The shared API pagination helper requests pages of 500, preserves selectors/scope, and uses a 30-second request timeout. Connections request gzip and use a short initial discovery timeout.
- Preserve bounded overview lists, deferred serialization where used, and the inexpensive `include_raw=False` summary paths. Do not eagerly serialize all event or pod manifests just to count them.
- Tables initially preserve API order. Column sizing samples at most 100 rows; filtering is debounced by 200 ms. Avoid introducing unbounded GUI-thread sizing or rendering work.

### Tables, ages, and labels

- Age sorting compares numeric durations, while display retains compact strings. Oldest-first examples: `1d`, `8h`, `35m`. Both sort directions work. Missing/unrecognized ages use a sort value of `-1`.
- Numeric CPU/memory values remain numeric for sorting, with separate display formatting.
- Nodes filter across visible values plus label and annotation keys/values. Cached metadata search text is rebuilt when rows change.
- Nodes show `karpenter.sh/nodepool` as `NodePool` immediately after Roles by default. A missing label displays an empty cell.
- Customizations has separate ordered label lists for Nodes and Pods. Pod label columns follow Name. Changes update existing workspaces, overview pod tables, and node-specific pod tabs, and apply to subsequently opened workspaces/tabs.
- Label-column model attributes use `label:<original-key>`. A short heading changes presentation only; lookups and tooltips retain the original Kubernetes key.
- The dialog accepts one key per line, optionally `key = Short heading`, for example:

```text
karpenter.sh/nodepool = Pool
app.kubernetes.io/name = App
example.com/team = Team
```

- Available label keys can be searched, added, and given an optional Short heading. Users can edit/reorder the list, clear it to remove columns, or restore defaults.
- Preserve active sort columns by attribute when inserting/removing/renaming label columns; a removed sort column falls back to unsorted order.

### Settings persistence

- `LabelSettings` uses `QSettings(IniFormat, UserScope, "Kubeman", "Kubeman")`.
- The `columns/labels` setting contains JSON: `Node` and `Pod` key arrays plus a `headers` map, separated by resource kind.
- Support earlier saved key lists without headings. Invalid saved configuration falls back to defaults. Default Node labels contain `karpenter.sh/nodepool`; Pod labels are empty.
- Heading changes are display-only and must never patch Kubernetes labels.
- Sync settings before updating in-memory preferences/emitting changes. Surface save errors to the user.
- The physical settings path is platform-specific; use `LabelSettings.store.fileName()` rather than hardcoding it.
- Tests must inject a QSettings instance backed by `tmp_path`; do not overwrite the user's actual preferences.

### YAML and secrets

- Details and Edit share `dump_manifest`. Preserve Unicode, dictionary order, multiline values, and trailing newlines. ConfigMap strings must survive a dump/load/edit cycle without changing their contents.
- Secret `raw` manifests mask `data`, `stringData`, and the last-applied-configuration annotation. Encoded `data` values are kept separately on `row.secret_data`.
- Decode values is a bounded, scrollable, case-insensitive key picker tested with 200 entries. Checkboxes preserve selection across filters; Decode selected reveals only those keys in Details (YAML).
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
- nsenter uses an interactive privileged `kubectl debug node/... --profile=sysadmin` pod, default namespace `default` and image `ubuntu:24.04`. Namespace/image are configurable. The image needs nsenter; this action requires a Linux node. The user is told to delete the debug pod afterward.
- List pods on this node opens/reuses `node-pods:<node>` with a server-side `spec.nodeName=<node>` selector across all namespaces. The workspace namespace filter intentionally does not restrict these tabs. They retain normal pod actions and label customization.

### Pod/container actions and terminals

- Pod menus support Logs, Shell, Attach, normal termination, and explicit Force terminate. Confirm mutations; force deletion uses zero grace period and can remove the API object before host processes stop. Finalizers still apply.
- Double-clicking a pod opens its containers, including regular, init, and ephemeral containers, with state, readiness, restart count, and image.
- Logs, Shell, and Attach must target the selected container, cluster context, namespace, and pod. Container-specific log tabs preserve the initial container selection.
- Commands use explicit `--context`; do not rely on the shell's active kubeconfig context.
- Windows terminals use `cmd /k` in a new console. macOS uses Terminal via osascript. Linux searches x-terminal-emulator, gnome-terminal, konsole, then xterm.
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
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_performance.py::test_large_table_initial_load_and_filter
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/benchmark_large_cluster.py --pods 10000
git diff --check
```

- `test_app.py` covers the app shell, navigation, views, resource mapping/actions, logs, scaling, and numeric formatting. New feature tests are split by the filenames above.
- Use fake services, synthetic Kubernetes objects, monkeypatched terminals, and local child processes. Local validation must not mutate a real cluster merely to exercise UI code.
- Wait for asynchronous callbacks/loading to finish before tearing down fixtures. Keep owning windows alive; QWidget/QObject ownership and pending events matter with PySide6.
- Qt enum comparisons should use the class enum, e.g. `QDialog.Accepted`, not `dialog.Accepted`. The latter caused an actual DrainDialog/NodeShellDialog traceback and was fixed.
- For popup-menu tests, a QTimer can select/close the menu inside its nested event loop; simply patching QMenu.exec did not reliably prevent a blocked popup in this environment.
- Keep display-role, edit/sort-role, and search-role behavior distinct. Cover both age sort directions and label aliases without changing label lookup keys.
- Automated checks use the offscreen Qt platform and mocked infrastructure. They do not prove live RBAC, drain success, nsenter permissions/image execution, cloud termination, or end-to-end terminal integration.

### Validation snapshot

The latest focused verification before this guide was 54 passing tests covering customization headings, age sorting, node filtering, and the app tests. The earlier broader feature run passed 95 targeted tests before the three heading-specific tests were added. Counts are historical snapshots; rerun the relevant tests after edits.

Full-suite verification on 2026-11-03 reported **105 passed, 5 failed**. All five failures are in `tests/test_performance.py` and were also observed earlier in this session:

- `test_overview_only_serializes_recent_events`
- `test_overview_pods_skips_manifest_serialization`
- `test_workload_overview_counts_pages_without_mapping_manifests[None]`
- `test_workload_overview_counts_pages_without_mapping_manifests[team]`
- `test_cluster_overview_uses_numeric_node_units_without_serializing`

These tests/fixtures expect older overview return shapes. Current `_overview_pods()` returns `(counts, lists)` and `_overview_events()` returns `(events, warning_count, warning_rows)`. Update assertions and fake return values deliberately if fixing these tests; do not remove the current drill-down data to satisfy stale expectations.

After the full-suite summary, Qt emitted `QObject: shared QObject was deleted directly. The program is malformed and may crash.` and the process exited with status 139. This full-suite cleanup problem remains unresolved. Focused feature runs have exited normally; do not present that as proof the full-suite cleanup issue is fixed.

## Change conventions

- Keep changes scoped to the requested behavior and preserve existing uncommitted work.
- Put shared table behavior in ResourceTable/TableModel, shared formatting in manifest.py, and service mutations in KubeService rather than duplicating UI-specific mechanisms.
- Keep blocking network/terminal-opening work off the GUI thread. Use existing Worker or QProcess patterns and respect workspace shutdown guards.
- Maintain Windows, macOS, and Linux launch/terminal paths when editing shared code; distinguish mocked coverage from actual OS verification.
- Keep runtime dependency declarations in pyproject.toml and requirements.txt consistent when changing dependencies.
- Do not commit credentials, kubeconfig data, real secret values, or per-user preferences.
- Report the checks actually run and any failures; do not label a targeted pass as a passing full suite.
- Documentation changes do not authorize deployment, cloud-instance termination, pushing branches, or live Kubernetes mutations.
