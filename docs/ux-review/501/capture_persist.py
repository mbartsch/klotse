"""#501 persistence: a Pods table whose Name column the user widened to 333 px, then the app starts again. Before (master): the
width is back to the default. After: it is kept. Fake rows; windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_persist.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.k8s.models import PodRow
from klotse.ui import resource_table
from klotse.ui.resource_table import ResourceTable

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
store = QSettings(str(Path(tempfile.mkdtemp()) / "klotse.ini"), QSettings.IniFormat)
if hasattr(resource_table, "use_settings"):  # master has no remembered widths
    resource_table.use_settings(store)
columns = [("Name", "name"), ("Status", "status"), ("Restarts", "restarts")]
rows = [PodRow(f"web-{i}-7d9f8c5b6-x{i}", "shop", "1/1", "Running", i, "", "") for i in range(6)]

first = ResourceTable(columns, "Pod")
first.resize(520, 220)
first.show()
first.set_rows(rows)
first.view.horizontalHeader().resizeSection(0, 333)  # the user drags the Name column
app.processEvents()
first.close()

second = ResourceTable(columns, "Pod")  # the next start
second.resize(520, 220)
second.show()
second.set_rows(rows)
for _ in range(10):
    app.processEvents()
print(mode, "Name column width on the next start:", second.view.columnWidth(0))
second.grab().save(out)
second.close()
