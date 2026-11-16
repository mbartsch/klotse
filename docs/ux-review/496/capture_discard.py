"""#496 structured editors: Esc after an edit. Before (master): the form closed at once and the edit was lost, so the picture is
the form with the edit, just before Esc. After: the question, with No as the default. Fake data only.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture_discard.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from klotse.ui.data_editor import DataEditDialog

CM = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "web", "namespace": "shop"}, "data": {"mode": "fast"}}
mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dlg = DataEditDialog("ConfigMap", dict(CM))
dlg.resize(640, 340)
dlg.show()
dlg.data.new_key.setText("token")
app.processEvents()
if mode == "before":
    dlg.grab().save(out)
else:
    box = QMessageBox(QMessageBox.Question, "Discard changes", "Discard the changes to this object?")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    box.resize(480, 160)
    box.show()
    app.processEvents()
    box.grab().save(out)
print("saved", out)
