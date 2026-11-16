"""#501 navigator width: the main window with the navigator asked to be 480 px wide. Before (master): the dock clamps to its
360 px maximum. After: it takes 480 px, so long alias rows fit. Fake services; windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_navigator_width.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
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
app.processEvents()
nav_dock = win.findChild(QDockWidget, "clustersDock")
win.resizeDocks([nav_dock], [480], Qt.Horizontal)
app.processEvents()
print(mode, "navigator width:", nav_dock.width())
win.grab().save(out)
win.shutdown()
