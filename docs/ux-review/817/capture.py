"""#817 captures with the Windows platform (text renders) and invented data: the tests' FakeService with made-up cluster
names, the four bundled cloud plugins enabled, no real kubeconfig and no cluster. Before = master's src, after = this
branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/817/capture.py OUTDIR

Captures: the cluster menu on a connected cluster (menu-connected), on a disconnected one added from the cloud
(menu-disconnected; "lab-oke" is marked removable as if it came from klotse-cloud.klotse) and the menu bar (menubar)."""
import os
import sys
import tempfile

from PySide6.QtCore import QSettings
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
        return ["prod-eks", "dev-gke", "lab-oke"], "prod-eks"


def snap(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(out, name + ".png"))
    widget.close()


win = MainWindow(Service, label_settings=LabelSettings(QSettings(os.path.join(home, "k.ini"), QSettings.IniFormat)))
for plugin_id in ("aws", "azure", "gcp", "oci"):
    win.plugins.set_enabled(plugin_id, True)
win.resize(1200, 760)
win.show()
win.open_cluster("prod-eks")
app.processEvents()
if hasattr(win.nav, "removable"):
    win.nav.removable = lambda context: context == "lab-oke"
snap(win.nav.menu_for(win.nav._clusters["prod-eks"]), "menu-connected")
snap(win.nav.menu_for(win.nav._clusters["lab-oke"]), "menu-disconnected")
app.processEvents()
win.menuBar().grab().save(os.path.join(out, "menubar.png"))
win.confirm_exit = lambda: True
win.close()
print("captured", out)
