"""#500 menus, rendered from the real code with fake data (no cluster).
  details  : the Details 'More…' popup for a pod with a dangerous entry (Force terminate) listed first in the input.
  several  : the View menu's Operations submenu, showing the 'Several clusters…' entry.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/500/capture_menus.py details|several OUT.png"""
import os
import sys
import tempfile
from types import SimpleNamespace as NS

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication, QToolButton

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])

if mode == "details":
    from klotse.ui.detail_panel import DetailPanel

    def act(title, dangerous=False):
        return NS(title=title, callback=lambda: None, dangerous=dangerous, primary=False, key=title)
    panel = DetailPanel()
    panel.set_object_actions([act("Edit…"), act("Force terminate…", True), act("Browse files…"),
                              act("Copy files…"), act("Port forward…"), act("Terminate…", True)])
    panel.show()
    button = next(b for b in panel.action_bar.findChildren(QToolButton) if b.menu() is not None)
    button.menu().popup(QPoint(120, 120))
else:
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService
    tmp = tempfile.mkdtemp()
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(os.path.join(tmp, "k.ini"), QSettings.IniFormat)))
    win.show()
    names = ("Several clusters…", "Multi Cluster…")  # the name after and before #500

    def find(menu):
        for a in menu.actions():
            if a.menu() is not None:
                hit = find(a.menu())
                if hit is not None:
                    return hit
            elif a.text() in names:
                return menu
        return None
    home = next(m for m in (a.menu() for a in win.menuBar().actions()) if find(m) is not None)
    find(home).popup(QPoint(120, 120))
app.processEvents()
popup = QApplication.activePopupWidget() or QApplication.topLevelWidgets()[-1]
popup.grab().save(out)
print("saved", out)
