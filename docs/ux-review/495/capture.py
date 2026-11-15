import os, sys
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from klotse.k8s.models import PodRow
from klotse.ui.detail_panel import DetailPanel
out = sys.argv[1]
app = QApplication.instance() or QApplication([])
panel = DetailPanel()
panel.resize(520, 160)
panel.show()
raw = {"kind": "Pod", "metadata": {"name": "web-1", "namespace": "team", "uid": "u1"}, "spec": {"containers": [{"name": "app", "image": "app:1"}]}, "status": {"phase": "Pending", "containerStatuses": [{"name": "app", "ready": False, "restartCount": 0, "state": {"waiting": {"reason": "ImagePullBackOff"}}}]}}
panel._set_identity(PodRow("web-1", "team", "0/1", "ImagePullBackOff", 0, "node-a", "1d", raw))
app.processEvents()
panel.grab().save(out)
print("saved", out, panel.identity_status.styleSheet())
