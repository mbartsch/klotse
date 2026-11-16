"""#498: Ctrl+Shift+F with no cluster open. Before (master): nothing happens and the status bar stays empty. After: the status bar
says to open a cluster first. Fake service only; no kubeconfig is read.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src:<tree>/tests python docs/ux-review/498/capture_status.py OUT.png"""
import os
import sys
import tempfile

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService

app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(os.path.join(tmp, "k.ini"), QSettings.IniFormat)))
win.resize(900, 420)
win.show()
app.processEvents()
win.search_action.trigger()  # the shortcut's action, with no cluster open
app.processEvents()
win.grab().save(sys.argv[1])
print("saved", sys.argv[1], "status:", win.status.currentMessage())
