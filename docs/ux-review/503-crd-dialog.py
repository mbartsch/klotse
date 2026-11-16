"""Renders the second question before a CustomResourceDefinition is deleted, with fake objects (no cluster).
Run: QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/503-crd-dialog.py OUT.png"""
import sys
from PySide6.QtWidgets import QApplication
from klotse.ui.crd_delete_dialog import CrdDeleteDialog

app = QApplication.instance() or QApplication([])
objects = [(f"team-{i % 4}", f"web-tls-{i:02d}", "2026-11-01T10:00:00Z") for i in range(24)] + [("", "cluster-cert", "2026-10-01T10:00:00Z")]
dialog = CrdDeleteDialog("Certificate", "certificates.cert-manager.io", objects)
dialog.show()
app.processEvents()
dialog.grab().save(sys.argv[1])
print("saved", sys.argv[1])
