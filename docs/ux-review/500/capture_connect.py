"""#500 (finish) capture: the "Cannot connect" notice names the cluster by its alias, as the tab does (Windows Qt platform,
invented data, no cluster). Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_connect.py OUTDIR"""
import os
import sys
import tempfile

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from test_app import FakeService  # noqa: E402

ini = os.path.join(tempfile.mkdtemp(), "klotse.ini")
win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(ini, QSettings.IniFormat)))
win.show()
if hasattr(win, "aliases"):
    win.aliases.set("arn:aws:eks:eu-west-1:111122223333:cluster/prod-eu", "Production EU")
win._on_failed("arn:aws:eks:eu-west-1:111122223333:cluster/prod-eu", "dial tcp: i/o timeout")
if hasattr(win, "_show_failures"):
    win._show_failures()
for _ in range(10):
    app.processEvents()
for number, box in enumerate([w for w in app.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()], 1):
    box.grab().save(os.path.join(out, f"cannot-connect-{number}.png"))
    box.close()
win.confirm_exit = lambda: True
win.close()
print("captured", out)
