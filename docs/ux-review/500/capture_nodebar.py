"""#500 node bar: Cordon asks Yes/No, so its label carries the ellipsis like the menu's. Before (master): "Cordon". After: "Cordon…".
Fake node only.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/500/capture_nodebar.py OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.node_bar import NodeActionBar

app = QApplication.instance() or QApplication([])
bar = NodeActionBar("node-a")
bar.resize(720, 80)
bar.show()
app.processEvents()
bar.grab().save(sys.argv[1])
print("saved", sys.argv[1])
