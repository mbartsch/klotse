"""#496 Enter in the add-key box. Before (master): Enter added the key AND pressed Save, so the dialog closed; a closed dialog
cannot be pictured, so the 'before' image is the dialog with the key typed, just before the Enter that closed it. After: the
key is listed and the dialog is still open. Fake data only.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture_enter.py before|after OUT.png"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from klotse.ui.data_editor import DataEditDialog

CM = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "web", "namespace": "shop"}, "data": {"mode": "fast"}}
mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dlg = DataEditDialog("ConfigMap", dict(CM))
dlg.resize(680, 380)
dlg.show()
dlg.data.new_key.setText("token")
if mode == "after":
    QTest.keyClick(dlg.data.new_key, Qt.Key_Return)
app.processEvents()
dlg.grab().save(out)
print("saved", out, "open:", dlg.isVisible())
