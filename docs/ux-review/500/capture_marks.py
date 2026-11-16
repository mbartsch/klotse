"""#500 part 19 captures (Windows Qt platform so text renders; no cluster, the menus are built from the code).
Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_marks.py OUTDIR"""
import os
import sys

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.action_bar import actions_button  # noqa: E402
from klotse.ui.pod_view import PodActionBar  # noqa: E402


def snap(widget, name):
    widget.show()
    app.processEvents()
    widget.grab().save(os.path.join(out, name + ".png"))
    widget.close()


# 1. The pod bar's More… menu: the pod row's entries, and the dangerous ones marked
bar = PodActionBar()
bar.resize(420, 60)
bar.show()
app.processEvents()
menu = bar.menu_button.menu()
menu.popup(bar.menu_button.mapToGlobal(bar.menu_button.rect().bottomLeft()))
app.processEvents()
menu.grab().save(os.path.join(out, "pod-bar-more-menu.png"))
menu.close()
bar.close()

# 2. A bar menu with a destructive entry: the trash mark (the same mark as the row menus)
button, _items = actions_button([("Edit…", "edit"), ("Terminate…", "terminate"), ("Force terminate…", "force-terminate")],
                                lambda name: None, text="More…")
button.show()
app.processEvents()
menu = button.menu()
menu.popup(button.mapToGlobal(button.rect().bottomLeft()))
app.processEvents()
menu.grab().save(os.path.join(out, "bar-menu-destructive-mark.png"))
menu.close()
button.close()
print("captured", out)
