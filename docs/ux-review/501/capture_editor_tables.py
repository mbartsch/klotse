"""#501 editor tables: the label/annotation table (KeyValueEditor) in a dialog 640x720 px. Before (master): fixed at 90 px,
leaving the rest of the dialog empty. After: at least 90 px and it grows with the dialog. Windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_editor_tables.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from klotse.ui.service_account_editor import KeyValueEditor

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dialog = QDialog()
dialog.setWindowTitle("Labels and annotations")
dialog.resize(640, 720)
layout = QVBoxLayout(dialog)
editor = KeyValueEditor("Labels", height=90)
editor.set_items({"app": "web", "tier": "frontend", "team": "shop"})
layout.addWidget(editor)
layout.addStretch(1)
dialog.show()
for _ in range(10):
    app.processEvents()
print(mode, "table min/max height:", editor.table.minimumHeight(), editor.table.maximumHeight(),
      "shown:", editor.table.height())
dialog.grab().save(out)
dialog.close()
