"""#496 pieces that are dialogs: renders the data editor after a refused key (before: the form only, silently) and the
question asked before closing a running terminal (after). Fake data only.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from klotse.ui.data_editor import DataEditDialog

CM = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "web", "namespace": "shop"}, "data": {"mode": "fast"}}
mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dlg = DataEditDialog("ConfigMap", dict(CM))
dlg.resize(640, 360)
dlg.show()
dlg.data.new_key.setText("mode")  # already a key here
if mode == "before":
    dlg.data._add()  # refused silently before #496
    app.processEvents()
    dlg.grab().save(out)
else:
    box = QMessageBox(QMessageBox.Warning, "Add key", "A key named “mode” already exists here. Choose another name, "
                      "or select it and change its value.")
    box.resize(520, 200)
    box.show()
    app.processEvents()
    box.grab().save(out)
print("saved", out)
