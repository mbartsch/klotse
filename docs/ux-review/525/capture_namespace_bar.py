"""#525 screenshot: the namespace bar of a workspace (fake cluster, invented namespace names).
   QT_QPA_PLATFORM=offscreen PYTHONPATH=src python shot525.py OUT.png"""
import sys
from types import SimpleNamespace as NS
from PySide6.QtWidgets import QApplication
from klotse.ui.workspace import ClusterWorkspace

out = sys.argv[1]
app = QApplication.instance() or QApplication([])
ws = ClusterWorkspace(NS(list_pods=lambda ns, **o: []), "demo")
ws.resize(900, 120)
ws.show()
ws.ns_combo.addItems(["shop", "data"])
ws.ns_combo.setCurrentIndex(ws.ns_combo.findText("default"))
for _ in range(5):
    app.processEvents()
ws.grab().save(out)
print("saved", out)
