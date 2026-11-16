"""#501 shortcuts: Help before (master): the Help menu holds About only. After: Help > Keyboard shortcuts opens a list read from
the menus. Fake services; windows platform, text drawn. The dialog and the menu are drawn on their own (grab), not from the screen.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_shortcuts.py before|after OUT.png"""
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
if mode == "after":
    from klotse.ui.shortcuts_dialog import ShortcutsDialog, shortcut_rows
    dialog = ShortcutsDialog(shortcut_rows(win.menuBar()))
    dialog.resize(560, 420)
    dialog.show()
    for _ in range(10):
        app.processEvents()
    dialog.grab().save(out)
    print(mode, "rows:", dialog.table.rowCount())
    dialog.close()
else:
    help_menu.show()
    for _ in range(10):
        app.processEvents()
    help_menu.grab().save(out)
    print(mode, "help entries:", [a.text() for a in help_menu.actions() if a.text()])
    help_menu.close()
win.shutdown()
