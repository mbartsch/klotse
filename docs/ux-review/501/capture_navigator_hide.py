"""#501 navigator hide: the main window with View > Show clusters panel off. Before (master): there is no way to hide the
navigator, so the window always shows it. After: the panel is hidden and the cluster area takes the width. Fake services;
windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_navigator_hide.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDockWidget

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_app import FakeService  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
settings = QSettings(str(Path(tempfile.mkdtemp()) / "klotse.ini"), QSettings.IniFormat)
win = MainWindow(FakeService, label_settings=LabelSettings(settings))
win.resize(1100, 650)
win.show()
if mode == "after":
    win.clusters_panel_action.setChecked(False)
app.processEvents()
nav_dock = win.findChild(QDockWidget, "clustersDock")
print(mode, "navigator hidden:", nav_dock.isHidden())
win.grab().save(out)
win.shutdown()
