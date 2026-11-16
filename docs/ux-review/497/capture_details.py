"""Renders the Details panel after a Find with no match and after copying the UID, with fake data (no cluster).
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/497/capture_details.py OUT.png
Before (master) shows no sign; after shows 'No match' and 'Copied UID'."""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.detail_panel import DetailPanel

app = QApplication.instance() or QApplication([])
panel = DetailPanel()
panel.resize(560, 300)
panel.show()
panel.editor.setPlainText("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: web\n  namespace: shop\ndata:\n  mode: fast\n")
panel.find_edit.setText("missing-text")
panel._find_next()
if hasattr(panel, "_copy_id_value"):
    panel._identity_uid = "5c0d-fake-uid"
    panel._copy_id_value("5c0d-fake-uid", "UID")  # the note 'Copied UID' appears under the identity
panel.yaml_tools.show()  # the Find bar and the identity header are hidden while nothing is selected
panel.identity_header.show()
panel.identity_uid.setText("UID: 5c0d-fake-uid")
panel.copy_uid_button.show()
app.processEvents()
panel.grab().save(sys.argv[1])
print("saved", sys.argv[1])
