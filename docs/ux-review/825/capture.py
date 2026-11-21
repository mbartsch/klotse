"""#825 captures with the Windows platform (text renders) and invented data: the tests' FakeService with two made-up
clusters, no real kubeconfig and no cluster. Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/825/capture.py OUTDIR

shop-prod shows its Pods tab, is collapsed in the cluster list, then dev-lab's tab and shop-prod's tab are chosen again:
tab.png is the window after that; expanded.png after expanding shop-prod in the list."""
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

from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from test_app import FakeService  # noqa: E402


class Service(FakeService):
    @staticmethod
    def load_contexts():
        return ["shop-prod", "dev-lab"], "shop-prod"


def settle(seconds=0.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


win = MainWindow(Service, label_settings=LabelSettings(QSettings(os.path.join(home, "k.ini"), QSettings.IniFormat)))
win.resize(1280, 720)
win.show()
win.open_cluster("shop-prod")
settle()
win.open_cluster("dev-lab")
settle()
shop = win.workspace("shop-prod")
win.clusters.setCurrentWidget(shop)
settle()
shop.open_view("pods")
settle(1.0)
win.nav._clusters["shop-prod"].setExpanded(False)
settle()
win.clusters.setCurrentWidget(win.workspace("dev-lab"))
settle()
win.clusters.setCurrentWidget(shop)  # what clicking shop-prod's tab does
settle(1.0)
win.grab().save(os.path.join(out, "tab.png"))
win.nav._clusters["shop-prod"].setExpanded(True)
settle()
win.grab().save(os.path.join(out, "expanded.png"))

win.confirm_exit = lambda: True
win.close()
print("captured", out)
