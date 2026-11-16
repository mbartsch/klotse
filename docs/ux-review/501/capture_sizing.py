"""#501 sizing: the kubeconfig entry dialog on a 600x400 screen. Before: opened at its fixed size, larger than the screen.
After: sized through dialog_sizing.bounded_size. The grey frame is the screen; text is not rendered offscreen.
  QT_QPA_PLATFORM=offscreen PYTHONPATH=<tree>/src python docs/ux-review/501/capture_sizing.py before|after OUT.png"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from klotse.ui.context_dialog import ContextEntryDialog
from klotse.ui.dialog_sizing import bounded_size

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
SCREEN_W, SCREEN_H = 600, 400
dialog = ContextEntryDialog("prod", ["cluster: prod", "server: https://example.invalid"])
if mode == "after":
    dialog.resize(*bounded_size(680, 300, SCREEN_W, SCREEN_H))
dialog.show()
app.processEvents()
canvas = QPixmap(SCREEN_W, SCREEN_H)
canvas.fill(QColor("#888888"))
shot = dialog.grab()
from PySide6.QtGui import QPainter
painter = QPainter(canvas)
painter.drawPixmap(0, 0, shot)
painter.end()
canvas.save(out)
print("saved", out, "dialog", dialog.width(), "x", dialog.height(), "screen", SCREEN_W, "x", SCREEN_H)
