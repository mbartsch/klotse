"""#501 tab bar: the main window with one cluster open (fake service). Before (master): the tab bar hides, with its Close and
Detach controls. After: the tab bar stays. Windows platform, so the text is drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_tabs.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

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
contexts, _active = win.new_service().load_contexts()
win.open_cluster(contexts[0])
app.processEvents()
print(mode, "tab bar auto-hide:", win.clusters.tabBarAutoHide(), "tabs:", win.clusters.count())
win.grab().save(out)
win.shutdown()
