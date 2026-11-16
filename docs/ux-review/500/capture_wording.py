"""#500 wording and menu captures, with the Windows platform (text renders) and invented data (no cluster: the test fake service).
Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_wording.py OUTDIR"""
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
from test_app import FakeService, open_active  # noqa: E402


def snap(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(out, name + ".png"))
    widget.close()


ini = os.path.join(tempfile.mkdtemp(), "klotse.ini")
win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(ini, QSettings.IniFormat)))
win.resize(1100, 720)
win.show()
open_active(win)
app.processEvents()

# 1. View > Settings > Auto-refresh across clusters (the three entries of the one action)
snap(win.auto_refresh_menu, "view-auto-refresh-menu")

# 2. The navigator's cluster menu: one entry for the state that applies, when the window can say which state it is in
ctx = win.clusters.currentWidget().context
menu = win.nav.menu_for(win.nav._clusters[ctx])
snap(menu, "navigator-cluster-menu")

# 3. New Namespace (the capitalised kind name, as in New Deployment)
snap(NamespaceDialog(), "new-namespace-dialog")
# closing the window asks 'Reconnect to the open cluster(s) the next time?' and waits for a click: the capture does not wait
win.confirm_exit = lambda: True
win.close()
print("captured", out)
