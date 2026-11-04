<p align="center">
  <img src="https://raw.githubusercontent.com/mbartsch/klotse/master/src/klotse/assets/klotse_logo.png" alt="Klotse logo" width="200">
</p>

# Klotse

A native Kubernetes desktop manager for Windows, macOS and Linux. Browse clusters, workloads, nodes and custom
resources; read logs; open shells; scale, cordon and drain, all from one window.

Built with Python, PySide6 (Qt Widgets) and the official Kubernetes Python client. It uses your existing
kubeconfig and authentication helpers, so there is nothing to sign in to and no server component.

> **Status:** early. Read-only use against real clusters has been tried; most of the
> mutating actions (cordon, drain, delete, scale) are covered by automated tests with fake services, not yet by
> live-cluster runs. Try them on a non-production cluster first.

## Features

- **Multiple clusters.** Every context in your kubeconfig is listed on the left; each open cluster gets its own
  tab and its own connection. Klotse never connects by itself: it starts with a clean list. When you exit with clusters
  open it asks whether to reconnect them next time (Yes remembers them, No starts clean, Cancel stays open).
- **Resources.** Overview, Workloads, Nodes, Namespaces, Pods, Deployments, ReplicaSets, StatefulSets, DaemonSets,
  Jobs, CronJobs, ConfigMaps, Secrets, Services, Ingresses, NetworkPolicies, PVCs, PVs, StorageClasses, Events,
  CustomResourceDefinitions, and read-only browsing of custom resource instances.
- **Always fresh.** The visible tab refreshes every 10 s (5/10/30/60 s selectable) and immediately when the
  cluster reports a change. Hidden tabs and clusters are not polled.
- **Details.** YAML view for any resource, with Secret values masked until you pick the keys to decode.
  `managedFields` are hidden by default (View → Show managedFields in details).
- **Pods and containers.** Logs (streaming, search, follow), Shell, Attach, terminate and force terminate.
  Double-click a pod for its own tab with every regular, init and ephemeral container.
- **Nodes.** Cordon/uncordon, drain (with the usual kubectl options), a privileged nsenter shell, delete. Double-click
  a node for its pods, with the node actions in a bar above them. Pod counts per node.
- **Bulk actions.** Select several rows (Ctrl/Shift) to scale, terminate, cordon or delete them in one confirmed
  step. Scale accepts a number or a relative change such as `*2`, `/2`, `+1`, `-1`.
- **Usage.** CPU and memory in use (from metrics-server, when present) on Nodes and Pods, plus optional request,
  limit and percent-of-request/limit columns on Pods.
- **Your columns.** The **Columns…** button on every list lets you hide and reorder built-in columns and add label
  columns with short headings. Saved per resource kind.
- **Edit menu and Preferences.** Undo/redo/cut/copy/paste/select all (Copy in a table copies the selected rows as
  tab-separated text). Preferences lets you choose a custom kubeconfig and open the settings folder.
- **macOS.** Choose Terminal or iTerm2 for shells (Settings → Terminal…).

## Requirements

- Python 3.10 or newer
- A kubeconfig with at least one context (or pick one in Edit → Preferences)
- [`kubectl`](https://kubernetes.io/docs/tasks/tools/) on your `PATH`, for drain, shells and attach
- Python dependencies (installed by the launchers): `PySide6-Essentials`, `kubernetes`, `PyYAML`

## Install

Native packages are built for Windows (an installer and a portable `.zip`) and macOS (a `.dmg` for Apple Silicon and
one for Intel). Version 0.1.0 is on the project's [Releases](https://github.com/mbartsch/klotse/releases) page, with
a `SHA256SUMS.txt` to check the downloads against. You can also run Klotse from source (below) or build the packages
yourself (see [Development](#development)).

- The Windows installer has been tested (install, run, uninstall). The macOS apps are built and self-tested in CI, and
  the 0.1.0 release has been tested on an Apple Silicon Mac (M3 Pro, macOS 26.7.1). The Intel build has not been run on an Intel Mac.
- The packages are not signed yet, so expect a warning on first start: on Windows choose **More info → Run anyway**,
  on macOS right-click the app and choose **Open**.
- `kubectl` is not included and has to be installed separately.

### Linux, or anywhere with Python 3.10+: pipx

There is no native Linux package yet. Install with [pipx](https://pipx.pypa.io), which keeps Klotse and its
dependencies in their own environment and puts a `klotse` command on your `PATH`:

```sh
pipx install git+https://github.com/mbartsch/klotse   # straight from GitHub (works today)
pipx install klotse                                   # from PyPI
klotse --install-desktop-entry                        # optional, Linux: add Klotse to the application menu
```

`klotse --uninstall-desktop-entry` removes the menu entry again. On a minimal Debian/Ubuntu install Qt may also need
these libraries for its X11 support (the project's CI installs exactly this list on Ubuntu and starts Klotse under
X11); most desktops already have them:

```sh
sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1
```

On other distributions install the equivalent packages. If Klotse stops with "could not load the Qt platform plugin
xcb", run `QT_DEBUG_PLUGINS=1 klotse` to see which library is missing.

## Run

macOS / Linux:

```sh
./run.sh              # creates .venv and installs dependencies on first run
./run.sh --check      # check that the imports work, without opening the window
./run.sh --install    # reinstall dependencies
```

Windows:

```bat
run.bat               # starts without a console window
run.bat --console     # keeps tracebacks visible
run.bat --check
run.bat --install
```

Or, in an existing environment:

```sh
pip install -r requirements.txt
PYTHONPATH=src python -m klotse
```

## Configuration

Settings are stored as a plain INI file, one section per resource kind and one entry per line:

```ini
[columns.Pod]
order.0=name
order.1=namespace
order.2=ready
hidden.0=cpu_req
hidden.1=cpu_lim
labels.0=app.kubernetes.io/name
labels_name.0=App
labels.1=example.com/team
```

`order` is the order of the built-in columns, `hidden` lists the ones that are hidden, and `labels` the label
columns; `labels_name.N` gives `labels.N` a short heading. A key that is missing means the default; an empty key
(`hidden=`) means none. You can edit the file by hand while Klotse is closed.

Open its folder from **Edit → Preferences… → Open config folder**. The same file holds the custom kubeconfig path
and the macOS terminal choice. A custom kubeconfig is passed both to the Kubernetes client and, as `--kubeconfig`,
to every `kubectl` command Klotse starts.

Settings from the earlier name of the project (Kubeman) are copied over on the first start.

## Development

```sh
pip install -r requirements-dev.txt
PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Tests run offscreen with fake services and synthetic Kubernetes objects; they never touch a real cluster or your
real settings. `run_tests.sh` / `run_tests.bat` wrap the same command.

After all tests pass the pytest process can still exit non-zero (a crash during Qt teardown in some test orders).
This is a known, unresolved issue that predates recent work, so check the pass count and the exit status separately.

To build the native packages (details, signing and releases in [`packaging/README.md`](https://github.com/mbartsch/klotse/blob/master/packaging/README.md)):

```sh
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1   # Windows: zip, plus installer if Inno Setup is installed
packaging/macos/build.sh                                               # macOS: Klotse.app and a .dmg
```

A packaged app can check itself: `Klotse --version` and `Klotse --self-test [REPORT_FILE]`. GitHub Actions builds
everything on a version tag (`v0.1.0`) or on demand and drafts a release.

[`AGENTS.md`](https://github.com/mbartsch/klotse/blob/master/AGENTS.md) documents the architecture, file map and behavior to preserve; read it before larger
changes. Work goes through pull requests from a branch; do not commit to `master` directly.

## Roadmap

Klotse is written in Python with PySide6 today. The idea is to move to Qt/C++ (or a similar language) at some
point. Nothing is decided and there is no timeline; the Python version is the one being developed in the meantime.

## License

Klotse is released under the [Apache License 2.0](https://github.com/mbartsch/klotse/blob/master/LICENSE).

Kubernetes is a registered trademark of The Linux Foundation. Klotse is an independent project and is not
affiliated with or endorsed by The Linux Foundation or the Kubernetes project.

## Author

Marcelo H. Bartsch · https://github.com/mbartsch/klotse
