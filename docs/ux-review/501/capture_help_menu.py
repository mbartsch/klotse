"""#501 help menu: the Help menu open in the main window (fake service). Before (master): About only. After: About, a separator,
the guide and the problem report links. Windows platform, so the text is drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_help_menu.py before|after OUT.png"""
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
help_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
help_menu.show()  # the menu itself, drawn on its own (a screen grab can miss a popup that is not in front)
for _ in range(20):
    app.processEvents()
help_menu.grab().save(out)
print(mode, "help entries:", [a.text() for a in help_menu.actions()])
help_menu.close()
win.shutdown()
