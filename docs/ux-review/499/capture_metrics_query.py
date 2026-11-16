"""#499 Metrics query window: a Close button and Esc. Before (master): no Close button. After: a Close button at the foot.
Fake source and presets only; no query is run.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/499/capture_metrics_query.py OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.metrics_query_dialog import MetricsQueryDialog

app = QApplication.instance() or QApplication([])
dialog = MetricsQueryDialog("Metrics query · prod-eu", "monitoring/prometheus-server:80", [], lambda *a, **k: None)
dialog.resize(720, 460)
dialog.show()
app.processEvents()
dialog.grab().save(sys.argv[1])
print("saved", sys.argv[1])
