"""#821 captures with the Windows platform (text renders) and invented data: the tests' FakeService with three made-up pods,
no real kubeconfig and no cluster. Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/821/capture.py OUTDIR

The Pods view after: click web-1, Ctrl+click web-1 (unselected), click web-1 again (reselect.png), and after selecting
web-1 and web-2 then Ctrl+clicking web-2 off (one-of-two.png). Details should show web-1 in both."""
import os
import sys
import tempfile
import time

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

home = tempfile.mkdtemp()
os.environ["USERPROFILE"] = os.environ["HOME"] = home  # no real home folder or user name in the images
app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.k8s.models import PodRow  # noqa: E402
from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from test_app import FakeService  # noqa: E402


class Service(FakeService):
    @staticmethod
    def load_contexts():
        return ["demo-cluster"], "demo-cluster"

    def list_pods(self, namespace=None):
        return [PodRow(name, "shop", "1/1", "Running", 0, "node-a", "2d",
                       {"kind": "Pod", "metadata": {"name": name, "namespace": "shop", "uid": "uid-" + name}})
                for name in ("web-1", "web-2", "web-3")]


def settle(seconds=0.4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def click(table, name, modifier=Qt.NoModifier):
    proxy_row = next(r for r in range(table.proxy.rowCount())
                     if table.model.rows[table.proxy.mapToSource(table.proxy.index(r, 0)).row()].name == name)
    QTest.mouseClick(table.view.viewport(), Qt.LeftButton, modifier, table.view.visualRect(table.proxy.index(proxy_row, 0)).center())
    settle(0.2)


win = MainWindow(Service, label_settings=LabelSettings(QSettings(os.path.join(home, "k.ini"), QSettings.IniFormat)))
win.resize(1280, 720)
win.show()
win.open_cluster("demo-cluster")
ws = win.workspace("demo-cluster")
ws.open_view("pods")
table = ws.pod_table
deadline = time.monotonic() + 10
while table.model.rowCount() < 3 and time.monotonic() < deadline:
    settle(0.1)
settle()
ws.tabs.setCurrentWidget(table)  # the cluster's Overview opens too and can end up in front
settle()

click(table, "web-1")
click(table, "web-1", Qt.ControlModifier)
click(table, "web-1")
settle()
win.grab().save(os.path.join(out, "reselect.png"))

click(table, "web-2", Qt.ControlModifier)
click(table, "web-2", Qt.ControlModifier)
settle()
win.grab().save(os.path.join(out, "one-of-two.png"))

win.confirm_exit = lambda: True
win.close()
print("captured", out)
