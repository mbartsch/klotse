"""#822 captures with the Windows platform (text renders) and invented data: the tests' FakeService with an invented
cluster, no real kubeconfig and no cluster. Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/822/capture.py OUTDIR

overview.png: the cluster Overview with nothing selected; overview-node.png: after picking node-a in its Node health table;
pods.png: the Pods tab with web-1 selected (Details is there as before)."""
import os
import sys
import tempfile
import time

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

home = tempfile.mkdtemp()
os.environ["USERPROFILE"] = os.environ["HOME"] = home  # no real home folder or user name in the images
app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.k8s.models import NodeRow, PodRow  # noqa: E402
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


def settle(seconds=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)  # what the app's event loop does: replaced Details buttons go


win = MainWindow(Service, label_settings=LabelSettings(QSettings(os.path.join(home, "k.ini"), QSettings.IniFormat)))
win.resize(1280, 720)
win.show()
win.open_cluster("demo-cluster")
ws = win.workspace("demo-cluster")
settle(1.0)
ws.open_view("overview")
settle(1.0)
win.grab().save(os.path.join(out, "overview.png"))

ws.overview._set_nodes([NodeRow("node-a", "Ready", "worker", "v1.30", "", 4, 8, "2d",
                                {"kind": "Node", "metadata": {"name": "node-a", "uid": "uid-node-a"}}, 1.2, 3.5)])
ws.overview.nodes_table.selectRow(0)
settle()
win.grab().save(os.path.join(out, "overview-node.png"))

ws.open_view("pods")
deadline = time.monotonic() + 10
while ws.pod_table.model.rowCount() < 3 and time.monotonic() < deadline:
    settle(0.1)
ws.pod_table.view.selectRow(0)
settle()
win.grab().save(os.path.join(out, "pods.png"))

win.confirm_exit = lambda: True
win.close()
print("captured", out)
