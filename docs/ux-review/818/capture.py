"""#818 captures with the Windows platform (text renders) and invented data: the tests' FakeService with a made-up EKS
cluster whose kubeconfig entry (a temporary file) signs in with `aws eks get-token`; the four bundled cloud plugins
enabled; no real kubeconfig, no cloud and no cluster. Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/818/capture.py OUTDIR

Captures: the cluster menu (menu), the settings dialog that menu opens for Google Cloud and for AWS (before: each
plugin's own dialog; after: the Cloud settings dialog with Google Cloud or Auto/AWS chosen)."""
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

home = tempfile.mkdtemp()
os.environ["USERPROFILE"] = os.environ["HOME"] = home  # no real home folder or user name in the images
app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from test_app import FakeService  # noqa: E402

config = Path(home, "config")
config.write_text(yaml.safe_dump({
    "contexts": [{"name": "prod-eks", "context": {"cluster": "c", "user": "u"}}],
    "clusters": [{"name": "c", "cluster": {"server": "https://EXAMPLE.gr7.eu-west-1.eks.amazonaws.com"}}],
    "users": [{"name": "u", "user": {"exec": {"command": "aws", "args": ["eks", "get-token", "--cluster-name", "prod"]}}}]}))


class Service(FakeService):
    @staticmethod
    def load_contexts():
        return ["prod-eks"], "prod-eks"


def grab_dialog(name, prepare=None):
    """Open the dialog the window shows for an entry, grab it, close it."""
    def grab():
        dialog = app.activeModalWidget()
        if dialog is not None:
            if prepare:
                prepare(dialog)
            app.processEvents()
            dialog.grab().save(os.path.join(out, name + ".png"))
            dialog.reject()
    QTimer.singleShot(500, grab)


win = MainWindow(Service, label_settings=LabelSettings(QSettings(os.path.join(home, "k.ini"), QSettings.IniFormat)))
for plugin_id in ("aws", "azure", "gcp", "oci"):
    win.plugins.set_enabled(plugin_id, True)
deadline = time.monotonic() + 20  # bundled plugins load on a worker: wait for all four before the menus are grabbed
while len({p.id for p in win.plugins.active()} & {"aws", "azure", "gcp", "oci"}) < 4 and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.05)
win.context_file = lambda context: str(config)
win.resize(1200, 760)
win.show()
win.open_cluster("prod-eks")
app.processEvents()
menu = win.nav.menu_for(win.nav._clusters["prod-eks"])
menu.show()
app.processEvents()
menu.grab().save(os.path.join(out, "menu.png"))
menu.close()

if hasattr(win, "CLOUD_SETTINGS"):
    grab_dialog("settings-aws")
    win.open_cluster_settings(win.CLOUD_SETTINGS, "prod-eks")
    grab_dialog("settings-gcp", lambda d: d.choice.setCurrentIndex(d.choice.findData("gcp")))
    win.open_cluster_settings(win.CLOUD_SETTINGS, "prod-eks")
else:
    grab_dialog("settings-aws")
    win.open_cluster_settings("aws", "prod-eks")
    grab_dialog("settings-gcp")
    win.open_cluster_settings("gcp", "prod-eks")
win.confirm_exit = lambda: True
win.close()
print("captured", out)
