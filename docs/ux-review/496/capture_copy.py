"""#496 Copy files: the dialog with a local file that does not exist. Before (master): OK closed the dialog and the error came
later, from the copy itself. After: the dialog stays open and says the file does not exist. Fake paths only.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture_copy.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.file_copy import FileCopyDialog, UPLOAD

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dialog = FileCopyDialog("web-1", "shop", ["app", "side"])
dialog.resize(640, 300)
dialog.show()
dialog.remote.setText("/data/")
dialog.local.setText("C:/Users/you/Documents/report-final.xlsx")  # does not exist
dialog.upload.setChecked(True)
if mode == "after":
    dialog._accept()  # the new check: the dialog stays open and explains
app.processEvents()
dialog.grab().save(out)
print("saved", out)
