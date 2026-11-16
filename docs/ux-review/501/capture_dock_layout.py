"""#501 dock layout: the navigator is hidden, the window closes, and a new start opens with the same settings. Before (master):
the dock layout is not kept, so the navigator is back. After: the hidden navigator stays hidden. Fake services; windows
platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_dock_layout.py before|after OUT.png"""
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
path = str(Path(tempfile.mkdtemp()) / "klotse.ini")
first = MainWindow(FakeService, label_settings=LabelSettings(QSettings(path, QSettings.IniFormat)))
first.resize(1100, 650)
first.show()
first.clusters_panel_action.setChecked(False)
first._remember_geometry()
first.shutdown()
second = MainWindow(FakeService, label_settings=LabelSettings(QSettings(path, QSettings.IniFormat)))
second.resize(1100, 650)
second.show()
for _ in range(10):
    app.processEvents()
nav_dock = second.findChild(QDockWidget, "clustersDock")
print(mode, "navigator hidden after restart:", nav_dock.isHidden())
second.grab().save(out)
second.shutdown()
