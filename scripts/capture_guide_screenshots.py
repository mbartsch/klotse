"""Render the screenshots used by the user guide in web/ with invented data and no cluster.

Run from the checkout with: PYTHONPATH=src python scripts/capture_guide_screenshots.py [--theme NAME]
The default theme (Harbour, dark) writes to web/screenshots/. --theme Daylight writes to
web/screenshots/daylight/, which the guide shows in its light mode.
Qt renders offscreen; settings are kept in a temporary directory. The main window uses the
fake service that the test suite uses (tests/test_app.py), so no cluster is needed.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from klotse.k8s import cloud_clusters, prometheus
from klotse.k8s.models import DeploymentRow
from klotse.ui import theme
from klotse.ui.customizations import LabelSettings
from klotse.ui.cloud_clusters_dialog import CloudClustersDialog
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.main_window import MainWindow
from klotse.ui.metrics_query_dialog import MetricsQueryDialog
from klotse.ui.palette_dialog import PaletteDialog
from klotse.ui.port_forward import Forward, PortForwardPanel, PortForwards
from klotse.ui.prometheus_source_dialog import PrometheusSourceDialog
from klotse.ui.saved_forwards import SavedForwards
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.aws.clusters import SOURCE as EKS
from test_app import FakeService, open_active  # the test suite's fake cluster service

OUTPUT = ROOT / "web" / "screenshots"


def capture(app, widget, filename, width, height):
    widget.resize(width, height)
    widget.show()
    # Let the fake cluster's background loads finish, so no picture shows a loading state.
    QThreadPool.globalInstance().waitForDone(5000)
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    path = OUTPUT / filename
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    print("wrote", path)
    widget.hide()


def settings(folder):
    return QSettings(str(Path(folder) / "klotse.ini"), QSettings.IniFormat)


def main_window(app, folder):
    win = MainWindow(FakeService, label_settings=LabelSettings(settings(folder)))
    open_active(win)
    return win


def metrics_query(app):
    series = [({"pod": "web-1", "container": "app"}, 0.42, 1700000000.0), ({"pod": "web-2", "container": "app"}, 0.37, 1700000000.0)]
    lines = {'pod="web-1", container="app"': [(1700000000 + 60 * i, 0.3 + 0.02 * ((i * 7) % 9)) for i in range(60)],
             'pod="web-2", container="app"': [(1700000000 + 60 * i, 0.25 + 0.015 * ((i * 5) % 7)) for i in range(60)]}

    def run(promql, ok, bad):
        ok(series)

    def run_range(promql, seconds, ok, bad):
        ok(lines)

    presets = prometheus.workload_queries("Deployment", "shop", "web")
    dialog = MetricsQueryDialog("Metrics: Deployment shop/web (prod-eu)", "monitoring/prom:9090", presets, run, run_range=run_range)
    dialog.set_source("monitoring/prom:9090")
    dialog.run_query()
    dialog.graph_range.setCurrentIndex(1)
    dialog.graph_query()
    return dialog


def source_dialog(app, folder):
    dialog = PrometheusSourceDialog("monitoring/my-prom:9090", "forward", 50000, "on", "", False, False)
    dialog.set_test_result(True, "monitoring/my-prom:9090 answers through the port-forward on 127.0.0.1:50000 (https, certificate errors ignored).")
    dialog.setWindowTitle("Prometheus or VictoriaMetrics")
    return dialog


def port_forwards(app, folder):
    manager = PortForwards(base=lambda context, kubeconfig: ["unused"])
    manager.items = [
        Forward(1, "prod-eu", "monitoring", "Service", "my-prom", 9090, 0, local=50000, state="active", origin="Usage history", connections=12),
        Forward(2, "prod-eu", "shop", "Pod", "web", 80, 8080, local=8080, state="active", connections=3),
        Forward(3, "prod-eu", "shop", "Pod", "worker", 5432, 0, state="stopped"),
    ]
    store = settings(folder)
    saved = SavedForwards(store)
    saved.add(SavedForwards.make("prod-eu", "Service", "monitoring", "my-prom", 9090, 0))
    panel = PortForwardPanel(manager, saved=saved, context="prod-eu")
    return panel


def cloud_clusters_dialog(app, folder):
    """Add clusters from the cloud, with the AWS CLI replaced by a fake that lists invented cluster names."""
    names = ["prod-eu-west", "staging-eu", "sandbox-us"]

    def fake_cli(argv, env=None, timeout=None):
        if argv[1:3] == ["eks", "list-clusters"]:
            return json.dumps({"clusters": names})
        raise RuntimeError("the picture only lists clusters")

    cloud_clusters.run = fake_cli  # the script's own process only; nothing reaches a cloud
    target = Path("C:/Users/example/.kube/klotse-cloud.klotse")  # shown only; nothing is written
    dialog = CloudClustersDialog(lambda: (str(target), False), [(AwsPlugin(), EKS)])
    dialog.edits["region"].setText("eu-west-1")
    dialog._list()
    deadline = time.monotonic() + 10
    while len(dialog._boxes) < len(names) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    for box in dialog._boxes[:2]:
        box.setChecked(True)
    return dialog


def command_palette(app, folder):
    """Ctrl+K over the fake cluster, with "pod" typed so the list is short and shows the matches."""
    win = main_window(app, folder)
    dialog = PaletteDialog(win.palette_entries(), win)
    dialog.box.setText("pod")
    return dialog


def details(app):
    raw = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop", "labels": {"app": "web"}},
           "spec": {"replicas": 3, "selector": {"matchLabels": {"app": "web"}}, "template": {"spec": {"containers": [
               {"name": "app", "image": "shop/web:1.4.2", "ports": [{"containerPort": 8080}]}]}}}, "status": {"replicas": 3, "readyReplicas": 3}}
    row = SimpleNamespace(raw=raw, name="web", namespace="shop", table_kind="Deployment", extra={})
    panel = DetailPanel()
    panel.show_row(row)
    panel.yaml_button.click()
    return panel


def main(argv=None):
    global OUTPUT
    parser = argparse.ArgumentParser(description="Render the user guide screenshots.")
    parser.add_argument("--theme", default=theme.DEFAULT.name, choices=[t.name for t in theme.BUILTIN],
                        help="built-in theme to render with (default: %(default)s)")
    args = parser.parse_args(argv)
    chosen = next(t for t in theme.BUILTIN if t.name == args.theme)
    if chosen is not theme.DEFAULT:
        OUTPUT = OUTPUT / chosen.name.lower()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # The Windows offscreen plugin does not always discover system fonts (same setup as capture_screenshots.py).
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    theme.apply(app, chosen)
    with tempfile.TemporaryDirectory() as folder:
        # The main window reads the saved theme from the (temporary) settings store.
        store = settings(folder)
        store.setValue("appearance/theme", chosen.name)
        store.sync()
        win = main_window(app, folder)
        capture(app, win, "main-window.png", 1280, 800)
        win.close()
        capture(app, details(app), "details-yaml.png", 620, 560)
        capture(app, source_dialog(app, folder), "source-dialog.png", 640, 560)
        capture(app, port_forwards(app, folder), "port-forwards.png", 900, 340)
        capture(app, metrics_query(app), "metrics-query.png", 900, 760)
        capture(app, cloud_clusters_dialog(app, folder), "cloud-clusters.png", 640, 520)
        capture(app, command_palette(app, folder), "command-palette.png", 640, 420)


if __name__ == "__main__":
    main()
