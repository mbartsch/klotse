"""#502 part 2 captures, with the Windows platform (text renders) and invented data (no cluster: the test fake service).
Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/502/capture_detached.py OUTDIR
The main window's exit question is never shown: the capture closes the window without asking."""
import os
import sys
import tempfile

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.customizations import LabelSettings  # noqa: E402
from klotse.ui.main_window import MainWindow  # noqa: E402
from test_app import FakeService, open_active  # noqa: E402


def settle(ms=60):
    app.processEvents()
    for _ in range(ms // 10):
        app.processEvents()


ini = os.path.join(tempfile.mkdtemp(), "klotse.ini")
win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(ini, QSettings.IniFormat)))
win.resize(1100, 720)
win.show()
open_active(win)
settle()
ws = win.clusters.currentWidget()
ctx = ws.context

# 1. Detached window: a message from the cluster appears in the window's own status bar (master: only in the main window)
assert win._detach_cluster(0)
detached = win._detached[ctx]
detached.resize(900, 520)
detached.show()
ws.message.emit("Loaded 42 pods from prod-eu")
settle()
detached.grab().save(os.path.join(out, "detached-window-status.png"))
detached.close()
settle()

# 2. Two clusters fail together (the VPN is down): one notice listing both (master: one box per cluster)
win._on_failed("eu-prod", "dial tcp: i/o timeout")
win._on_failed("us-prod", "dial tcp: i/o timeout")
if hasattr(win, "_show_failures"):
    win._show_failures()
settle(200)
boxes = [w for w in app.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
for number, box in enumerate(boxes, 1):
    box.grab().save(os.path.join(out, f"connect-failures-{number}.png"))
print(f"{len(boxes)} notice(s)")
for box in boxes:
    box.close()
win.confirm_exit = lambda: True
win.close()
print("captured", out)
