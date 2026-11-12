"""Render the screenshots used by the user guide in web/ with invented data and no cluster.

Run from the checkout with: PYTHONPATH=src python scripts/capture_guide_screenshots.py
Qt renders offscreen; settings are kept in a temporary directory. The main window uses the
fake service that the test suite uses (tests/test_app.py), so no cluster is needed.
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from klotse.k8s import prometheus
from klotse.k8s.models import DeploymentRow
from klotse.ui import theme
from klotse.ui.customizations import LabelSettings
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.main_window import MainWindow
from klotse.ui.metrics_query_dialog import MetricsQueryDialog
from klotse.ui.port_forward import Forward, PortForwardPanel, PortForwards
from klotse.ui.prometheus_source_dialog import PrometheusSourceDialog
from klotse.ui.saved_forwards import SavedForwards
from test_app import FakeService, open_active  # the test suite's fake cluster service

OUTPUT = ROOT / "web" / "screenshots"


def capture(app, widget, filename, width, height):
    widget.resize(width, height)
    widget.show()
    for _ in range(3):
        app.processEvents()
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


def details(app):
    raw = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop", "labels": {"app": "web"}},
           "spec": {"replicas": 3, "selector": {"matchLabels": {"app": "web"}}, "template": {"spec": {"containers": [
               {"name": "app", "image": "shop/web:1.4.2", "ports": [{"containerPort": 8080}]}]}}}, "status": {"replicas": 3, "readyReplicas": 3}}
    row = SimpleNamespace(raw=raw, name="web", namespace="shop", table_kind="Deployment", extra={})
    panel = DetailPanel()
    panel.show_row(row)
    panel.yaml_button.click()
    return panel


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    # The Windows offscreen plugin does not always discover system fonts (same setup as capture_screenshots.py).
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    theme.apply(app)
    with tempfile.TemporaryDirectory() as folder:
        win = main_window(app, folder)
        capture(app, win, "main-window.png", 1280, 800)
        win.close()
        capture(app, details(app), "details-yaml.png", 620, 560)
        capture(app, source_dialog(app, folder), "source-dialog.png", 640, 560)
        capture(app, port_forwards(app, folder), "port-forwards.png", 900, 340)
        capture(app, metrics_query(app), "metrics-query.png", 900, 760)


if __name__ == "__main__":
    main()
