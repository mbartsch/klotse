"""Capture the namespace selector before and after #767.

Usage: QT_QPA_PLATFORM=offscreen PYTHONPATH=src python capture_namespace_bar.py before|after OUT.png
"""
import sys
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.workspace import ClusterWorkspace


mode, output = sys.argv[1:3]
app = QApplication.instance() or QApplication([])
theme.apply(app, theme.HARBOUR)
workspace = ClusterWorkspace(NS(list_pods=lambda namespace, **options: []), "demo")
workspace.resize(900, 120)
workspace.show()
workspace.ns_combo.addItems(["shop", "data"])
workspace.ns_combo.setCurrentIndex(workspace.ns_combo.findText("default"))
if mode == "before":
    workspace.ns_dropdown_action.setVisible(False)
for _ in range(5):
    app.processEvents()
workspace.grab().save(output)
print("saved", output)
