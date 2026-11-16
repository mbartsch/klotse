"""#500 (remaining items) capture: the node bar's More… menu with the full "Force delete node…" label (Windows Qt platform,
no cluster; the menu is built from the code). Usage: QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python capture_nodebar.py OUTDIR"""
import os
import sys

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.node_bar import NodeActionBar  # noqa: E402

from types import SimpleNamespace as NS
bar = NodeActionBar(NS(name="worker-1", raw={"metadata": {"uid": "u1"}, "spec": {}}, status="Ready", cpu="", memory="", pods="", age="1d", roles="worker", version="v1", internal_ip="10.0.0.1", ready=True, cpu_pct=0, memory_pct=0, pod_count=0, cpu_capacity=4.0, memory_capacity=8.0, taints=[]))
bar.resize(640, 80)
bar.show()
app.processEvents()
menu = bar.menu_button.menu()
menu.popup(bar.menu_button.mapToGlobal(bar.menu_button.rect().bottomLeft()))
app.processEvents()
menu.grab().save(os.path.join(out, "node-bar-more-menu.png"))
print("captured", out)
