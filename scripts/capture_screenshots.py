"""Render README screenshots with invented data and no cluster connection.

Run from the checkout with: PYTHONPATH=src python scripts/capture_screenshots.py
Qt renders offscreen; settings are kept in a temporary directory.
"""
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QThreadPool
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel

from klotse.k8s import health
from klotse.k8s.models import PodRow
from klotse.plugins.manager import PluginManager
from klotse.ui import theme
from klotse.ui.health_dialog import HealthDialog
from klotse.ui.metrics_dialog import MetricsDialog
from klotse.ui.overview import POD_COLUMNS_BASIC
from klotse.ui.preferences import PreferencesDialog
from klotse.ui.resource_table import ResourceTable


OUTPUT = Path(__file__).resolve().parents[1] / "screens"


def capture(app, widget, filename, width, height):
    widget.resize(width, height)
    widget.show()
    app.processEvents()
    widget.clearFocus()
    app.processEvents()
    path = OUTPUT / filename
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    widget.hide()
    print(path.name)


def pod_table():
    table = ResourceTable(POD_COLUMNS_BASIC, "Pod")
    table.setWindowTitle("Pods — invented demo data")
    table.set_header_widget(QLabel("Pods · demo-shop (invented data)"))
    rows = []
    for index, (name, status, restarts) in enumerate([
        ("api-6d9b7f8c45-2rkml", "Running", 0),
        ("api-6d9b7f8c45-8wxpv", "Running", 2),
        ("checkout-748fc9d85b-4vkns", "CrashLoopBackOff", 12),
        ("checkout-748fc9d85b-q9mzt", "Running", 6),
        ("worker-5b6c9df7d8-n2rpk", "Running", 8),
        ("worker-5b6c9df7d8-t7lws", "Running", 0),
        ("payments-6d8c47f95b-x5jmn", "ImagePullBackOff", 4),
        ("cache-0", "Running", 0),
    ]):
        rows.append(PodRow(name, "demo-shop", "1/1" if status == "Running" else "0/1",
                           status, restarts, f"demo-worker-{index % 3 + 1}", "2h",
                           cpu_used=0.06 + index * 0.035, mem_used=96 + index * 22))
    table.set_rows(rows)
    table.filter.setText("Restarts > 3")
    table._apply_filter()
    table.view.resizeColumnsToContents()
    return table


def usage_chart():
    dialog = MetricsDialog("Usage history — invented demo data", lambda: {},
                           refs={"cpu": {"request": 0.25, "limit": 0.75},
                                 "mem": {"request": 256, "limit": 512}}, available=False)
    # Fixed timestamps make captures reproducible. These points imitate samples;
    # they are never presented as measurements from a real metrics-server.
    start = 1794218400
    for index in range(61):
        dialog.history.add({"app": (start + index * 15,
                                    0.22 + math.sin(index / 6) * 0.055 + index * 0.0015,
                                    210 + math.sin(index / 9) * 24 + index * 1.2)}, start + index * 15)
    dialog.available = True
    dialog.pause_button.setChecked(True)
    dialog._redraw()
    dialog.status.setText("Invented demo data · 61 samples at 15 s intervals · paused")
    return dialog


def health_check():
    dialog = HealthDialog("demo-cluster (invented data)", "demo-shop")
    dialog.show_findings([
        health.finding(health.ERROR, "Pod", "demo-shop", "checkout-748fc9d85b-4vkns",
                       "container app: CrashLoopBackOff", "Read the previous container logs and check its configuration."),
        health.finding(health.ERROR, "Pod", "demo-shop", "payments-6d8c47f95b-x5jmn",
                       "container app: ImagePullBackOff", "Check the image name, tag and registry credentials."),
        health.finding(health.WARN, "Node", "", "demo-worker-3",
                       "DiskPressure", "Free disk space or move workloads to another node."),
        health.finding(health.WARN, "Deployment", "demo-shop", "worker",
                       "container app has no resource limits", "Without limits it can use all of a node's CPU or memory."),
        health.finding(health.WARN, "PersistentVolumeClaim", "demo-shop", "uploads",
                       "Pending", "Check the storage class and available persistent volumes."),
        health.finding(health.INFO, "Deployment", "demo-shop", "checkout",
                       "container app has no readiness probe", "Traffic can reach it before it is ready."),
    ])
    return dialog


def main():
    app = QApplication([])
    # The Windows offscreen plugin does not always discover system fonts.
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))
    theme.apply(app)
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="klotse-screenshots-") as folder:
        store = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
        capture(app, pod_table(), "08-pods-filter-expression.png", 1180, 270)
        capture(app, usage_chart(), "09-usage-history.png", 1000, 660)
        capture(app, health_check(), "10-health-check.png", 1380, 460)
        manager = PluginManager(store, folder=Path(folder) / "plugins", entry_points=False)
        preferences = PreferencesDialog(SimpleNamespace(path="", missing=""), folder, plugins=manager)
        preferences.tabs.setCurrentWidget(preferences.plugins_panel)
        capture(app, preferences, "11-preferences-plugin-manifest.png", 1040, 720)
        assert manager.get("aws").plugin is None, "Discovery must not execute the AWS plugin"
        QThreadPool.globalInstance().waitForDone()
        for widget in app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()
