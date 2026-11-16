"""#501 Overview cut-off: the cluster Overview at 1280 px wide with the Details panel open (fake service). Before (master):
the header row is clipped on the right, with no horizontal scrollbar. After: the page scrolls sideways instead.
Windows platform, so the text is drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_overview_width.py before|after OUT.png"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QScrollArea

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_app import FakeService  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
settings = QSettings(str(Path(tempfile.mkdtemp()) / "klotse.ini"), QSettings.IniFormat)
win = MainWindow(FakeService, label_settings=LabelSettings(settings))
win.resize(1280, 800)
win.show()
app.processEvents()
contexts, _active = win.new_service().load_contexts()
win.open_cluster(contexts[0])
for _ in range(40):
    app.processEvents()
ws = win.clusters.currentWidget()
scroll = ws.overview.findChildren(QScrollArea)
bar = scroll[0].horizontalScrollBar() if scroll else None
print(mode, "overview scroll policy:", scroll[0].horizontalScrollBarPolicy() if scroll else None,
      "h range:", (bar.minimum(), bar.maximum()) if bar else None)
win.grab().save(out)
win.shutdown()
