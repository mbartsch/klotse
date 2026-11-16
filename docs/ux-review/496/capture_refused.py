"""#496 refused save. Before (master): the error box, and the changes were gone. After: the same error box, then the question
offering to open the editor again with the changes. Text is the code's own; no cluster.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture_refused.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
text = "prod-eu\n\nThe cluster refused this (409 Conflict).\nServer: Operation cannot be fulfilled on configmaps \"web\": the object has been modified\nRefresh, then try again."
if mode == "before":
    box = QMessageBox(QMessageBox.Warning, "Kubernetes error", text)
else:
    box = QMessageBox(QMessageBox.Question, "Changes not saved", "Open the editor again with the changes you made?")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
box.resize(600, 220)
box.show()
app.processEvents()
box.grab().save(out)
print("saved", out)
