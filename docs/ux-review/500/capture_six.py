"""#500 six items: the destructive question with the action on its button, and the debug dialog with a required field
(Windows Qt platform, invented data, no cluster). Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/500/capture_six.py OUTDIR"""
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.node_dialogs import DebugDialog  # noqa: E402

TEXT = "Cluster: Production EU (arn:aws:eks:eu-west-1:111:cluster/prod)\nDelete pod shop/web-1?"

try:  # this branch: the verb on the button
    from klotse.ui.confirm import ORIGINAL as ask_destructive

    def ask():
        return ask_destructive(None, "Delete pod", TEXT, verb="Delete")
except ImportError:  # master: Yes and No
    def ask():
        return QMessageBox.question(None, "Delete pod", TEXT, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)


def grab_modal(name):
    """Save the modal box that the question shows, then close it: the question call itself blocks until it is answered."""
    def take():
        box = app.activeModalWidget()
        if box is not None:
            box.grab().save(os.path.join(out, name + ".png"))
            box.done(0)
    QTimer.singleShot(400, take)


grab_modal("delete-question")
ask()

# the debug dialog with the image cleared: master disables Open; this branch keeps it and names the empty field on click
dialog = DebugDialog("prod-eu", "shop/web-1", ["app"])
dialog.resize(560, 300)
dialog.show()
dialog.image.setText("")
app.processEvents()
dialog.grab().save(os.path.join(out, "debug-dialog-empty-image.png"))
dialog.close()
print("captured", out)
