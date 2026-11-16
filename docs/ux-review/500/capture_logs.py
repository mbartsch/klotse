"""#500 logs tabs: the single-pod and several-pod toolbars side by side (Windows Qt platform, invented data, no cluster).
Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_logs.py OUTDIR"""
import os
import sys

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.log_view import LogView  # noqa: E402
from klotse.ui.multi_log import MultiLogView  # noqa: E402


class Stream:
    def stream(self, n):
        return iter([b"app started\n", b"request ok\n", b"request error\n"])

    def close(self):
        pass


class Service:
    def open_log_stream(self, namespace, pod, container=None, tail=500, **options):
        return Stream()


def snap(widget, name, width=900, height=260):
    widget.resize(width, height)
    widget.show()
    for _ in range(20):
        app.processEvents()
    widget.grab().save(os.path.join(out, name + ".png"))
    widget.close()


single = LogView(Service(), "shop", "web-1", ["app"])
snap(single, "single-pod-logs")
several = MultiLogView(Service(), "shop", lambda selector: [], lambda fn, on_result, on_error=None: None)
snap(several, "several-pod-logs")
print("captured", out)
