# Fixes from the code review of 2026-11-13

Findings from a read-through of the code for security, performance and best practices. Nothing was run for this review: no
tests, linters or live cluster. Every finding comes from reading the code, and line numbers are from `master` at `3e772ca`.

No single release-blocking defect was found in the files that were read. The 12 items under "Important" should be fixed
before calling this production-ready.

## Coverage

About a third of the codebase was read.

- **Read in full:** `k8s/client.py`, `ui/terminal.py`, `ui/terminal_session.py`, `ui/pty_session.py`,
  `ui/embedded_terminal.py` and its page script, `ui/port_forward.py`, `ui/file_copy.py`, `k8s/remote_files.py`,
  `ui/file_browser.py`, `ui/command_view.py`, `cli.py`, `__main__.py`, `ui/environment.py`, `desktop_entry.py`,
  `plugins/manager.py`, `plugins/manifest.py`, `k8s/helm.py`, `k8s/drain_plan.py`, `k8s/audit.py`, `ui/audit_log.py`,
  `workers.py`, `ui/watcher.py`, `ui/table_model.py`, `ui/settings_store.py`, `.github/workflows/release.yml`, and the AWS
  plugin's `safe.py`, `awscreds.py`, `federation.py`, `native.py`, `console.py`.
- **Read in part:** `ui/workspace.py` (loading, shutdown, edit/delete, bulk, node, pod and terminal paths),
  `ui/main_window.py` (exit only), `k8s/prometheus.py`, `ui/plugin_actions.py`, `ui/log_view.py`, AWS `__init__.py` and
  `pricing.py`.
- **Not reviewed:** `ui/resource_table.py`, `ui/overview.py`, `ui/navigator.py`, all form editors, `ui/filter_expr.py`,
  `ui/customizations.py`, most dialogs, the pure analysis modules under `k8s/`, `scripts/`, the PyInstaller spec, and the
  tests. The form editors and `resource_table.py` are the largest gaps.

## Important

- [ ] **1. Terminal input can freeze the app.** `ui/pty_session.py:103` writes to the pty on the GUI thread (wired at
  `ui/embedded_terminal.py:132`). A large paste into a program that is not reading stdin can block `os.write` and hang the
  window. Not reproduced on a real run. Fix: write from a thread with a bounded queue.
- [ ] **2. `pty.fork()` in a multithreaded Qt process.** `ui/pty_session.py:56` forks, then runs Python (`execvpe` PATH
  search) in the child. If another thread held a lock at fork time, the child deadlocks as a copy of the GUI process. Fix:
  `subprocess.Popen` with `pty.openpty()` and `start_new_session=True`.
- [ ] **3. No output flow control.**
  - Terminal output is emitted in 64 KB chunks with no backpressure (`ui/pty_session.py:79`), and `TerminalTab._pending`
    grows without bound before the page loads.
  - Logs are emitted one signal per 1 KB (`ui/log_view.py:30`).
  - A chatty pod or `cat` of a big file can flood the event queue. Fix: batch on a timer and use xterm's write callback.
- [ ] **4. Most writes have no timeout, on a pool sized to CPU count.**
  - In `k8s/client.py`, every create, replace, patch (except `cordon_node`), scale, restart, rollback, CronJob trigger and
    server-side apply lacks `_request_timeout`. So does `open_log_stream`, which has no connect timeout.
  - All workspaces and plugins share `QThreadPool.globalInstance()` (`ui/workspace.py:172`), which also runs the 5-minute
    `helm uninstall`.
  - A few hung calls on a 4-core machine stall every cluster. Fix: add timeouts and give I/O its own larger pool.
- [ ] **5. Watches retry forever at a fixed interval.** `k8s/client.py:1077` waits 2 s after any `ApiException`, including
  401/403. A user allowed to list but not watch sends a list and a watch every 2 s per view, indefinitely. Fix: stop on
  401/403 and back off exponentially.
- [ ] **6. Node shell leaves no trace and can leave a privileged pod behind.** `node-shell` (`ui/workspace.py:3097`) creates
  a privileged hostPID pod but never calls `record_change`. If the terminal is killed, `kubectl run --rm` cannot clean up.
  Shell, Attach, port-forward and dangerous plugin actions are also missing from the change log (only Helm, copy, drain and
  debug record). Fix: record them, and give the pod a label and `activeDeadlineSeconds`.
- [ ] **7. The Secrets list downloads every secret value.** `map_secret` (`k8s/resources.py:87`) keeps `s.data` on each
  row. Opening the list for all namespaces transfers and holds every Secret in the cluster. Fix: list metadata only (as
  Helm and Unused already do) and fetch one Secret on Decode or Edit.
- [ ] **8. Exit does not warn about running operations.** `confirm_exit` (`ui/main_window.py:1370`) only asks about
  reconnecting. A running drain, `kubectl cp` or bulk worker is killed silently, and `__main__.py:75` hard-exits after 3 s.
- [ ] **9. Predictable shared temp folder on Linux.** `ui/theme.py:150` uses `/tmp/klotse-icons`. Another local user can
  pre-create it and plant the PNGs, or a dangling symlink that `image.save` writes through. Fix: use the per-user cache
  location.
- [ ] **10. AWS `Credentials` prints its secrets.** `klotse_plugins/aws/federation.py:35` is a plain dataclass, so `repr()`
  includes the secret key and token, unlike `AwsCredentials`. No code was found that logs it today. Fix: `repr=False`.
- [ ] **11. `delete_node` accepts a missing UID.** `k8s/client.py:1000` builds `V1Preconditions(uid=None)`, which is no
  precondition. The UI checks first, but `delete_resource` and `delete_custom` refuse in the service and this one should
  too. `delete_pod` has no UID precondition at all.
- [ ] **12. Scale has no cluster name or confirmation.** The prompts at `ui/workspace.py:2832` and `:2938` do not show the
  context, and typing `0` applies at once. Every other destructive action names the cluster and asks Yes/No.

## Minor bugs

- [ ] **Helm dialog:** closing it during an uninstall makes `dialog.show_status` / `dialog.accept` hit a deleted widget
  (`ui/workspace.py:1094-1100`), so the list is not refreshed.
- [ ] **Paging:** `k8s/client.py:1021` does `yield from page.items`, which fails if a server returns `"items": null`; the
  line above already uses `or []`. A 410 on an expired continue token is not retried.
- [ ] **Scale monitor** (`KubeService.scale_status`, `k8s/client.py:1807`):
  - It lists pods and all Warning events of the namespace unpaged on every poll.
  - The sort key can be `None`, which the bare `except` turns into "no warnings".
  - It ignores `matchExpressions`.
- [ ] **Port forward** (`ui/port_forward.py`): `Forward.scheme` is never read, so "Open in browser" always uses http.
  Finished `QProcess` objects are never deleted. The table is rebuilt on every "Handling connection" line.
- [ ] **File copy** (`ui/file_copy.py`): `_tick` walks the download folder on the GUI thread every second. `posixpath` is
  imported but unused.
- [ ] **macOS terminal:** `_mac_session_command` is computed and then overwritten (`ui/terminal.py:164`, `:184`). A
  `TimeoutExpired` or missing `osascript` leaves the temp script behind.
- [ ] **Linux/Windows terminal:** `Popen` objects are never reaped (`ui/terminal.py:156`, `:198`).
- [ ] **Windows pty read loop:** `continue` on empty data may spin (`ui/pty_session.py:77`). Unconfirmed: what pywinpty
  returns there was not checked.
- [ ] **Embedded terminal bridge:** `Bridge.ready` does not range-check sizes (`ui/embedded_terminal.py:73`), and
  `struct.error` is not caught in `PtySession.resize`.
- [ ] **Workers:** `Worker.run` (`workers.py`) does not catch `SystemExit`, so a plugin calling `sys.exit` leaves the view
  loading.
- [ ] **Plugin settings:** `_persist_enabled` (`plugins/manager.py`) wipes the whole `[plugins]` group, which will drop any
  future key there.
- [ ] **Delete flow:** `delete_resource` (`ui/workspace.py:2800`) asks for confirmation before checking the UID.

## Hardening and maintainability

- [ ] **kubectl arguments:** pod and node names are passed as bare positionals (`exec -it NAME`). A hostile API server
  could return a name starting with `-`. Use `pod/NAME` or validate names.
- [ ] **Prometheus source** (`k8s/prometheus.py`, `KubeService._prometheus_get`):
  - The path prefix is put into the API-server proxy path unquoted; reject `..`, `?` and `#`.
  - Many pod names in one GET can exceed URL limits.
  - For https forwards, verifying against the service DNS name would avoid the need for "Ignore certificate errors".
- [ ] **Rich text:** `<b>{self.context}</b>` headers (`ui/workspace.py:1761`, `:2300`, `:2353`, `:2390`,
  `ui/trivy_dialog.py:97`, `ui/cluster_settings_dialog.py:19`) and tooltips built from cluster strings are unescaped. The
  impact is display spoofing only.
- [ ] **Metadata-only lists:** the `Accept` header falls back to `application/json`, so a server that ignores the first
  type would send full Secrets despite the docstring (`_metadata_names`, `_metadata_list`, `list_helm_releases`).
- [ ] **AWS plugin:**
  - `urllib` follows redirects carrying the signed headers.
  - The price cache write is not atomic (`pricing.py:208`); the instance cache already is.
  - The secret key sits in plaintext in the INI (documented in the UI); an OS keyring would be better.
- [ ] **Change log:** it is described as append-only but rotates at 1 MB keeping one old file, can be cleared, and records
  no user identity.
- [ ] **Supply chain:** dependencies are lower bounds only, so release builds are not reproducible. Add a hashed
  constraints file, and record the vendored xterm.js version.
- [ ] **Docs:** `AGENTS.md` omits `PySide6-Addons`, which `pyproject.toml` and `requirements.txt` require.
- [ ] **Structure:**
  - `ui/workspace.py` is a 3,500-line class.
  - `shutdown` discovers its windows through a dozen `getattr(self, "_x_dialog", None)` calls; a registry would be safer.
  - About 30 nested `dialog.exec()` loops run inside signal handlers.
  - `0x08000000` is repeated in four places instead of `subprocess.CREATE_NO_WINDOW`.

## Done well

- Commands are argv lists throughout with explicit `--context`.
- The dry run fails closed, the read-only guard has two layers, and deletes carry UID preconditions.
- The terminal page has a strict CSP and locked navigation.
- The AWS code validates regions and refuses DTDs.
- Workflow actions are SHA-pinned with explicit permissions.
