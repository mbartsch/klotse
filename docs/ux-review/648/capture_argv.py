"""#648 follow-up: the Arguments box with an empty value, a space-only value and a value with a line break (Windows Qt platform,
invented data). Before = master's src, after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/648/capture_argv.py OUTDIR"""
import os
import sys

from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.workload_widgets import LinesEdit  # noqa: E402

ARGS = ["--config", "", "  ", "a\nb"]
form_host = QWidget()
form = QFormLayout(form_host)
box = LinesEdit(ARGS, "Arguments; one per line")
form.addRow("Arguments", box)
form_host.resize(420, 120)
form_host.show()
app.processEvents()
form_host.grab().save(os.path.join(out, "arguments-box.png"))
form_host.close()
print("captured", out)
