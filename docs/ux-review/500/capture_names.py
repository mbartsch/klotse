"""#500 names and forms: the status bar after a failed connection (the alias in the message), and the New Namespace dialog with
its required Name (Windows Qt platform, invented data, no cluster). Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_names.py OUTDIR"""
import os
import sys
import tempfile

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from klotse.ui.namespace_dialog import NamespaceDialog  # noqa: E402
from test_app import FakeService  # noqa: E402

CONTEXT = "arn:aws:eks:eu-west-1:111122223333:cluster/prod-eu"
ini = os.path.join(tempfile.mkdtemp(), "klotse.ini")
win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(ini, QSettings.IniFormat)))
win.resize(900, 300)
win.show()
if hasattr(win, "aliases"):
    win.aliases.set(CONTEXT, "Production EU")
win._on_failed(CONTEXT, "dial tcp: i/o timeout")
for _ in range(10):
    app.processEvents()
win.statusBar().grab().save(os.path.join(out, "status-after-failed-connection.png"))
win._connection_prompts.get(CONTEXT) and win._connection_prompts[CONTEXT].close()
win.confirm_exit = lambda: True
win.close()

dialog = NamespaceDialog()
dialog.resize(480, 260)
dialog.show()
app.processEvents()
dialog.grab().save(os.path.join(out, "new-namespace-dialog.png"))
dialog.close()
print("captured", out)
