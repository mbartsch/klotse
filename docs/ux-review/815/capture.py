"""#815 captures with the Windows platform (text renders) and invented data: a temporary klotse-cloud.klotse with made-up
clusters, no real kubeconfig and no cluster. Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/815/capture.py OUTDIR

Captures: the cluster list's menu on a cluster added from the cloud (cloud-menu), on two selected ones (cloud-menu-two),
and, where the branch has it, the Remove from Klotse confirmation (remove-confirm)."""
import os
import sys
import tempfile
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

home = Path(tempfile.mkdtemp())
os.environ["USERPROFILE"] = os.environ["HOME"] = str(home)  # Path.home(): no real home folder or user name in the images
app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui import main_window as main_window_module  # noqa: E402
from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402

generated = home / ".kube" / "klotse-cloud.klotse"
generated.parent.mkdir()
names = ["lab-eks-1", "lab-eks-2", "lab-aks-1"]
generated.write_text(yaml.safe_dump({
    "apiVersion": "v1", "kind": "Config", "current-context": names[0],
    "clusters": [{"name": n, "cluster": {"server": f"https://{n}.example.test"}} for n in names],
    "users": [{"name": f"user-{n}", "user": {"token": "invented"}} for n in names],
    "contexts": [{"name": n, "context": {"cluster": n, "user": f"user-{n}"}} for n in names]}), encoding="utf-8")
main_window_module.default_generated_path = lambda home=None: generated


class FileService:
    """Context names from the file it is pointed at; the default kubeconfig has one invented cluster."""

    def __init__(self):
        self.kubeconfig = None
        self.proxy, self.no_proxy = "", []

    def load_contexts(self):
        if self.kubeconfig:
            return [c["name"] for c in yaml.safe_load(Path(self.kubeconfig).read_text())["contexts"]], None
        return ["demo-main"], None


def snap(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(out, name + ".png"))
    widget.close()


win = MainWindow(FileService, label_settings=LabelSettings(QSettings(str(home / "klotse.ini"), QSettings.IniFormat)))
win.kubeconfig_settings.add_extra(str(generated))
win.contexts = win._load_contexts()
win.nav.set_contexts(win.contexts, win.groups, win.aliases.aliases)
win.resize(1100, 720)
win.show()
app.processEvents()

item = win.nav._clusters["lab-eks-1"]
snap(win.nav.menu_for(item), "cloud-menu")
for name in ("lab-eks-1", "lab-eks-2"):
    win.nav._clusters[name].setSelected(True)
snap(win.nav.menu_for(item), "cloud-menu-two")

if hasattr(win, "remove_cloud_clusters"):
    def grab_and_cancel():
        box = app.activeModalWidget()
        if isinstance(box, QMessageBox):
            box.grab().save(os.path.join(out, "remove-confirm.png"))
            box.reject()
    QTimer.singleShot(500, grab_and_cancel)
    win.remove_cloud_clusters(("lab-eks-1", "lab-eks-2"))

win.confirm_exit = lambda: True
win.close()
print("captured", out)
