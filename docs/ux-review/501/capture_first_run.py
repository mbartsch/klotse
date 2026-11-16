"""#501 first run: the main window with no cluster open. Before (master): a blank centre. After: the getting-started page.
Fake services only (no kubeconfig is read), offscreen (no text drawn; the text is in the test file).
  QT_QPA_PLATFORM=offscreen PYTHONPATH=<tree>/src python docs/ux-review/501/capture_first_run.py before|after OUT.png OUT_DIR"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests"))
from test_app import FakeService  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
folder = tempfile.mkdtemp()
settings = QSettings(str(Path(folder) / "klotse.ini"), QSettings.IniFormat)
win = MainWindow(FakeService, label_settings=LabelSettings(settings))
win.resize(1100, 650)
win.show()
app.processEvents()
win.grab().save(out)
print(mode, "open clusters:", win.clusters.count())
win.shutdown()
