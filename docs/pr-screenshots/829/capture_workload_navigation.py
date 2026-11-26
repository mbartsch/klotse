"""Capture #829 on a Windows display with fake workload data.

Set PYTHONPATH to the chosen checkout's src directory. Run with
--before on the base and without it on the PR head.
"""
import argparse
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from kubernetes import client
from PySide6.QtCore import QCoreApplication, QPoint, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from klotse.k8s.client import map_replicaset
from klotse.k8s.models import PodRow
from klotse.ui import theme
from klotse.ui.workspace import ClusterWorkspace


def capture(window, path):
    screen = window.screen()
    bounds = window.frameGeometry()
    if not screen.geometry().contains(bounds):
        raise RuntimeError("The capture window must fit on one screen")
    bounds.translate(-screen.geometry().topLeft())
    if not screen.grabWindow(0, bounds.x(), bounds.y(), bounds.width(), bounds.height()).save(str(path)):
        raise RuntimeError(f"Could not save {path}")


def wait_until(app, condition):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.02)
    raise RuntimeError("The expected workspace state did not appear")


def capture_workload(app, output, before):
    rs = map_replicaset(client.V1ReplicaSet(
        metadata=client.V1ObjectMeta(name="web", namespace="shop", uid="rs-uid"),
        spec=client.V1ReplicaSetSpec(selector=client.V1LabelSelector(), replicas=1),
        status=client.V1ReplicaSetStatus(replicas=1, ready_replicas=1),
    ))
    pod = PodRow("web-a", "shop", "1/1", "Running", 0, "worker-01", "1d", {
        "metadata": {"name": "web-a", "namespace": "shop", "uid": "pod-uid",
                     "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                          "name": "web", "uid": "rs-uid", "controller": True}]},
    })
    workspace = ClusterWorkspace(SimpleNamespace(read_workload_owner=lambda _: [rs]), "demo")
    workspace.setWindowTitle("Klotse — demo")
    workspace.setWindowFlag(Qt.WindowStaysOnTopHint)
    workspace.resize(1080, 700)
    workspace.move(workspace.screen().availableGeometry().center() - workspace.rect().center())
    workspace.ns_combo.addItem("shop")
    workspace.set_namespace("shop")
    workspace._show_view("pods")
    workspace.show()
    workspace.raise_()
    workspace.activateWindow()
    table = workspace.pod_table
    table.set_rows([pod])
    wait_until(app, workspace.isActiveWindow)
    stage = "before" if before else "after"
    menu_errors = []

    def capture_menu():
        menu = app.activePopupWidget()
        try:
            if menu is None:
                raise RuntimeError("The Pod row menu did not open")
            action = next((a for a in menu.actions() if a.text() == "Go to ReplicaSet web"), None)
            if (action is None) != before:
                raise RuntimeError("Unexpected workload navigation action")
            menu.move(workspace.mapToGlobal(QPoint(270, 0)))
            menu.setActiveAction(action)
            QTest.qWait(250)
            capture(workspace, output / f"{stage}-pod-menu.png")
            if action is not None:
                action.trigger()
        except Exception as exc:
            menu_errors.append(exc)
        finally:
            if menu is not None:
                menu.close()

    QTimer.singleShot(350, capture_menu)
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    if menu_errors:
        raise menu_errors[0]
    if before:
        workspace._show_view("replicasets")
        workspace.rs_table.set_rows([rs])
        workspace.rs_table.view.selectRow(0)
    wait_until(app, lambda: workspace._detail_row is rs)
    QTest.qWait(300)
    capture(workspace, output / f"{stage}-replicaset.png")
    QThreadPool.globalInstance().waitForDone(5000)
    workspace.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        QCoreApplication.setOrganizationName("KlotseScreenshot")
        QCoreApplication.setApplicationName("WorkloadNavigation")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
        app = QApplication([])
        if app.platformName() != "windows":
            raise RuntimeError("A native Windows display is required")
        theme.apply(app, theme.DEFAULT)
        capture_workload(app, args.output, args.before)


if __name__ == "__main__":
    main()
