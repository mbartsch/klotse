"""#496 Metrics source: the dialog with a mistyped address. Before (master): OK closed the dialog and lost the text, so the
picture is the dialog as it was just before OK. After: the warning box, and the dialog stays open. Fake data only.
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/496/capture_metrics.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from klotse.ui.metrics_preferences import AddSourceDialog, valid_source

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
dialog = AddSourceDialog(["prod"], cluster="prod", service="monitoring/prom:9090", fixed_cluster=True)
dialog.resize(640, 360)
dialog.show()
dialog.service.setEditText("not a valid address !!")
app.processEvents()
if mode == "before":
    dialog.grab().save(out)
else:
    try:
        valid_source(dialog.service.currentText().strip())
        text = "unexpected: accepted"
    except ValueError as error:
        text = str(error)
    box = QMessageBox(QMessageBox.Warning, "Metrics source", text)
    box.resize(560, 200)
    box.show()
    app.processEvents()
    box.grab().save(out)
print("saved", out)
