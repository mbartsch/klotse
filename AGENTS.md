# Klotse project guide

Applies to the repository rooted here. It records the current implementation and working conventions; verify details against source when changing them. The previous long version (per-feature verification notes, measurements) is in git history.

## Where the detail lives

Read these before the work they cover; they hold the detail moved out of this file, unchanged:

- [Architecture and file map](docs/agents/architecture-and-file-map.md): file map, feature table, resource kinds: read before changing code or adding a feature
- [UI and behavior to preserve](docs/agents/ui-behavior.md): UI and behaviour rules to preserve: read before any change in `src/klotse/ui/`
- [Packaging](docs/agents/packaging.md): PyInstaller, releases, installer: read before packaging or release work
- [Session hand-off (updated 2026-11-12)](docs/agents/session-handoff.md): current state, open issues, owner's workflow notes: read at the start of a session
- [CI and platform notes (2026-11-12)](docs/agents/ci-and-platforms.md): CI shards, platform notes, Qt testing traps: read before changing workflows or tests

The namespace selector is an editable, searchable combo box. Keep its explicit trailing dropdown affordance (`namespaceDropdownAction`) wired to the existing popup while preserving typing, completion and keyboard selection.

## Project and local state

Preferences > General owns the persisted application font family and point-size controls. Applying them must update the running Qt application and regenerate theme-scaled styles without replacing the fixed-width fonts used by code and manifest editors.

- Klotse is a native Kubernetes desktop manager: Python + PySide6/Qt Widgets, the Kubernetes Python client and `kubectl`. No web frontend, database or server.
- Package `klotse`, Python `>=3.10`, setuptools `src/` layout. The version is one line in `src/klotse/_version.py` and changes only in a release PR (see Packaging).
- Runtime deps: `PySide6-Essentials>=6.6`, `PySide6-WebEngine>=6.6` (embedded terminal; about 300 MB, accepted by the owner), `kubernetes>=29`, `PyYAML>=6`, `pywinpty>=2.0.1` on Windows, `PySocks>=1.7` (SOCKS proxies for the Kubernetes client). Dev deps add `pytest`, `pytest-qt`. Keep `pyproject.toml` and `requirements.txt` consistent.
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

## Tests and verification

Pre-commit (`.pre-commit-config.yaml`): run `pre-commit install` once per clone; the hooks refuse commits on `master`, run bandit and zizmor (versions pinned to `security.yml`), and check whitespace. `pre-commit run --all-files` runs them on demand. The test suite is not a hook.

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

## Security checks

- `.github/workflows/security.yml` runs bandit (`bandit -r src scripts -ll`; fix or justify with `# nosec Bxxx - reason`), pip-audit, zizmor and dependency review; `codeql.yml` runs CodeQL; Dependabot updates pip and Actions weekly. `SECURITY.md` is the reporting policy.
- Workflows: pin every action to a commit SHA with the tag in a comment, set `permissions` explicitly, use `persist-credentials: false` on read-only checkouts, and pass untrusted text (PR titles, branch names) through `env:`, never inline in `run:`.
- Data from a cluster is untrusted: validate it before it builds an address or a command.

## Change conventions

- Owner's workflow: one branch and one squash-merged PR per feature, no stacked PRs; commits end with the `Co-Authored-By` line and PR bodies with the "Generated with Claude Code" line from the session reminder; every feature PR updates this file and `ROADMAP.md` and states what was and was not verified. **Standing instruction: merge each PR as soon as every check passed** (checks exist, none pending or failed, PR head equals local HEAD, all tested in the merge command); re-run checks that failed because GitHub itself failed (`gh run rerun <id> --failed`), never merge over them.
- **Worktrees are mandatory.** Each branch and PR gets its own `git worktree` under `G:\kube-worktrees\<branch-slug>` (create it with `git worktree add -b <branch> <path> origin/master`). Never switch the main checkout `G:\kube` to a feature branch; it stays on `master` and is only used for merges and pulls. Remove a worktree after its PR is merged.
- **UX issue workflow (sub-issues of #231):** one branch and one PR per issue, named after the issue. Before changing code, capture a before screenshot of the affected view on the real Windows display (not offscreen; fake data, never the owner's real clusters) and attach it to the PR; after the change, capture the same view again and attach it too. Both go in the PR body as images pinned to the commit SHA, with the capture script committed beside them (see the `klotse-feature` skill). When the change affects contrast or icons, include the relevant dark theme in the captures. Each issue's checklist items get one offscreen regression test each. Merge the PR as soon as every check is green, under the standing instruction above. Tick the issue's checkboxes in the same PR, and update `ROADMAP.md`.

- Keep changes scoped to the request and preserve existing uncommitted work. Update this guide when behavior or the file map changes, concisely.
- Shared table behavior goes in ResourceTable/TableModel, shared formatting in `manifest.py`, service mutations in `KubeService`.
- Keep blocking network and terminal-opening work off the GUI thread (existing Worker or QProcess patterns, respect shutdown guards).
- Keep Windows, macOS and Linux launch/terminal paths working; distinguish mocked coverage from actual OS verification.
- Do not commit credentials, kubeconfig data, real secret values or per-user preferences.
- Documentation changes do not authorize deployment, cloud-instance termination, pushing branches or live Kubernetes mutations.
- Pending, not merged at the time of writing: main menu grouping (#232), actionable load states (#234), manifest filter paths (#215; described above), optional All Clusters overview implementation (#531).

- #817: a cluster's menu (`Navigator._fill_cluster_menu`) has four groups with one separator between non-empty groups: connection; Rename alias…/Move to group; the cluster's settings (plugin entries, Metrics source…, auto-refresh: connected only); Show kubeconfig entry…/Sign in…/Remove from Klotse…. There is no Plugins menu bar entry.

- #815: Remove from Klotse… in the cluster menu forgets disconnected clusters read from `klotse-cloud.klotse` only (`MainWindow.is_removable_cloud_cluster`, `cloud_clusters.remove_contexts`: shared cluster/user entries stay, `.bak` kept); it never edits the main kubeconfig or touches the cloud.

- #777: keep the optional All clusters page hidden until added to its workspace tab; an unmanaged visible child intercepts menu-bar mouse input.

- #379: the metrics source Add/Edit dialog offers Test, metrics-server only and Use the global default. Test uses unsaved values, requires HTTP-auth acknowledgement, discards stale replies and never saves settings.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

Azure plugin #63 is WIP (help appreciated): metadata-only Node columns and validated public-portal VM/VMSS links; no prices/cost cards or live AKS verification. Remaining work: #783.

GCP plugin #64 is WIP (help appreciated): metadata-only Node columns and validated Compute console links; prices/cost cards and live-cloud verification remain in #785. Missing capacity stays unknown; OS image is not a Compute boot-image resource.

OCI plugin #458 is WIP (help appreciated): terminal sign-in validates region/profile before opening; no live OCI/browser/MFA/reconnect verification. Further validation and potential contributions: #787.

#432 (core only): plugin API 13 adds metric sources (`MetricSource`/`MetricSeries`, `Plugin.metrics(MetricsRequest)`, manifest `metric_sources`), offered in Usage history for one resource of the declared kinds; `plugins.host.read_metrics` runs on the window's worker and bounds/validates the answer. No bundled plugin uses it; AWS CloudWatch is #800.

#790: cloud discovery keeps GCP clusters distinct by name and location; missing-location credential lookup refuses ambiguous results. AWS/GCP/OCI parsers preserve first-seen order while deduplicating. Optional local configuration defaults remain best-effort. No live-cloud verification.

#795: a failing kubeconfig sign-in helper fails the connection with its exit code and stderr (`connect_help.helper_failures`; the Popen guard turns a terminal-launch `None` stderr into "", which was the `'NoneType' ... strip` error) instead of a later TLS/401 error. GCP import with `CLOUDSDK_API_ENDPOINT_OVERRIDES_CONTAINER` on loopback/`*.localhost` (Floci) replaces the gke-gcloud-auth-plugin exec with a fixed test token, only for loopback servers; the emulator's CA is kept, and a cluster without one is refused. No real Floci/GCP verification.
#377: the bundled Teleport plugin (off until enabled) is a cluster source for Add clusters from the cloud: `tsh kube ls --format=json`, then `tsh kube login <name>` with KUBECONFIG set to Klotse's private temporary file; optional proxy and tsh path are dialog fields. Untrusted names (leading `-`, control characters) are refused. JSON field names and live Teleport are unverified.
#796: AKS credentials pass `--context <cluster>`; the Azure plugin renames generic (`default`/empty) cluster, user and context entries in the CLI output to the selected cluster (`clusterUser_<group>_<name>` for users) and rewrites references, so repeated imports stay distinct. Specific Azure names are untouched. Not verified against real Azure or Floci.

## Local Terraform lab (#793)

The local-only Floci lab in `labs/floci/` defines two clusters with one two-node pool per provider, Docker Compose startup, teardown and separate short-lived Kubernetes identities for Klotse. Use a dedicated daemon; loopback-only published ports are recommended, and `start.py` and `identity.py` warn (not refuse) when Docker publishes beyond 127.0.0.1. `lab.py up|down|env` wraps startup, Terraform apply/destroy and the shell variables; `down --purge` skips Terraform (emulator state lives in Docker; a destroy can hang on the AWS VPC) and removes the Floci-created objects compose never sees: every `floci_namespace=klotse793` container, volume and network, and the unlabelled `floci-<emulator>-klotse793-…` ones (the EKS k3s data volumes; a stale one makes a new sidecar exit with `failed to find interface with specified node ip`) and the local Terraform state, even after a failed destroy; with Azure selected, `up` and `tunnel` register the FlociLab cloud in the private `AZURE_CONFIG_DIR` (explicit AD endpoint with the port, `core.instance_discovery=false`) and sign `az` in with floci-az's test service principal, warning and skipping without `az`; `lab.py aks-kubeconfig [FILE]` rewrites floci-az's unreachable AKS servers (`https://floci-az-klotse793-aks-<id>:6443`, the k3s container name) in Klotse's cloud import file to `https://localhost:<docker port>`, keeping the CA (the k3s certificate names localhost); the AWS root creates the IAM user `klotse-local` and an access key that `up` saves to `.local/aws-key.json` and `env` exports instead of `test`/`test` (Floci's EKS token webhook refuses root-signed `aws eks get-token` tokens with 401); `up` refuses before starting anything when docker, terraform or (with `--identities`) kubectl is not on PATH, and identity.py reports a missing kubectl/docker as a plain message; `-v`/`--debug` logs each health attempt and command, `--tf-log` sets TF_LOG; floci-gcp's health path is `/_floci-gcp/health`. A remote lab: `lab.py tunnel user@host` (SSH forwards on loopback, recommended), or `up --bind` plus `env --host` for the cloud CLIs only (sets KLOTSE_INSECURE_NET; cluster APIs still need the tunnel) (offline unit tests only). Validated on an isolated Linux daemon: all eight contexts worked through Klotse's Kubernetes service; AWS needed a documented import recovery; `lab.py up` now does it (Floci 2.5.0 drops the first CreateCluster connection in a new VPC while attaching itself to the VPC network, the SDK retry gets 409; `up` imports the missing lab clusters, waits for ACTIVE and applies the node groups). Each emulator exposed one Ready k3s node; native GUI interaction and cloud sign-in are not verified.
