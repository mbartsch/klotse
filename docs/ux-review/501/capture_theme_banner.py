"""#501 theme change: a cluster in read-only mode (the warning banner), then View > theme switched to Daylight with no restart.
Before (master): the banner keeps the old warning colour. After: it takes Daylight's. Fake services; windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_theme_banner.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_app import FakeService, open_active  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
settings = QSettings(str(Path(tempfile.mkdtemp()) / "klotse.ini"), QSettings.IniFormat)
win = MainWindow(FakeService, label_settings=LabelSettings(settings))
win.resize(1100, 650)
win.show()
open_active(win)
ws = win.clusters.currentWidget()
ws.set_read_only(True)
app.processEvents()
win.apply_theme("Daylight")
app.processEvents()
print(mode, "banner style:", ws.read_only_label.styleSheet()[:60])
win.grab().save(out)
win.shutdown()
