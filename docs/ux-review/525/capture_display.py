"""#525 screenshots on a real display (Windows): the namespace bar and the open selector with check boxes.
   Fake cluster and invented namespace names. Run with QT_QPA_PLATFORM unset (the native Windows platform).
   PYTHONPATH=src python capture_display.py OUT_BAR.png OUT_POPUP.png [ticks]"""
import sys
from types import SimpleNamespace as NS
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from klotse.ui.workspace import ALL_NS, DEFAULT_NS, ClusterWorkspace

bar_out, popup_out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
ws = ClusterWorkspace(NS(list_pods=lambda ns, **o: []), "demo")
ws.resize(900, 420)
ws.show()
ws._fill_namespace_combo([DEFAULT_NS, "shop", "data", "billing"], DEFAULT_NS)
if hasattr(ws, "_apply_picked"): ws._apply_picked(ALL_NS)
if hasattr(ws, "_tick_namespace"):
    ws._tick_namespace("shop")
    ws._tick_namespace("data")
for _ in range(10):
    app.processEvents()
ws.grab().save(bar_out)
print("bar", bar_out, ws.ns_combo.lineEdit().text())
ws.ns_combo.showPopup()
for _ in range(10):
    app.processEvents()
view = ws.ns_combo.view()
view.window().grab().save(popup_out)
print("popup", popup_out)
ws.ns_combo.hidePopup()
