"""#525 screenshot: the namespace selector with several names ticked (fake cluster, invented namespace names).
   QT_QPA_PLATFORM=offscreen PYTHONPATH=src python docs/ux-review/525/capture_checks.py OUT.png"""
import sys
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QApplication

from klotse.ui.workspace import ALL_NS, DEFAULT_NS, ClusterWorkspace

out = sys.argv[1]
app = QApplication.instance() or QApplication([])
ws = ClusterWorkspace(NS(list_pods=lambda ns, **o: []), "demo")
ws.resize(900, 120)
ws.show()
names = [DEFAULT_NS, "shop", "data", "billing"]
ws._fill_namespace_combo(names, DEFAULT_NS)
ws._apply_picked(ALL_NS)
ws._tick_namespace("shop")
ws._tick_namespace("data")
for _ in range(5):
    app.processEvents()
ws.grab().save(out)
print("saved", out)
