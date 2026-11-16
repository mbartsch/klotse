"""Renders the error box before and after #497, and the new Activity panel, with fake data (no cluster).
Run with the Windows Qt platform so text is drawn:
  QT_QPA_PLATFORM=windows PYTHONPATH=src python docs/ux-review/497/capture.py before|after|activity OUT.png
'before' builds the text the old _on_error built; 'after' uses the wording from klotse.ui.load_errors."""
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from klotse.ui.activity_log import Activity, ActivityPanel

CONFLICT = ('ApiException: (409)\nReason: Conflict\nHTTP response headers: HTTPHeaderDict({"Audit-Id": "f3a"})\n'
            'HTTP response body: {"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure",'
            '"message":"Operation cannot be fulfilled on deployments.apps \\"web\\": the object has been modified",'
            '"reason":"Conflict","code":409}')
CONTEXT = "prod-eu"


def box(title, text):
    b = QMessageBox(QMessageBox.Warning, title, text)
    b.resize(640, 260)
    b.show()
    return b


mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
if mode == "before":
    b = box("Kubernetes error", f"{CONTEXT}\n\n{CONFLICT}")
elif mode == "after":
    from klotse.ui.load_errors import describe
    title, text = describe(CONFLICT)
    b = box(title, f"{CONTEXT}\n\n{text}\n\nThe complete error is in View > Operations > Activity.")
else:
    from datetime import datetime
    activity = Activity(clock=lambda: datetime(2026, 11, 15, 9, 30, 5))
    activity.add(True, "Deleted Pod web-7")
    activity.add(False, "The cluster refused this (409 Conflict). Server: Operation cannot be fulfilled on deployments.apps",
                 CONFLICT)
    panel = ActivityPanel(activity)
    panel.resize(760, 300)
    panel.show()
    b = panel
app.processEvents()
b.grab().save(out)
print("saved", out)
