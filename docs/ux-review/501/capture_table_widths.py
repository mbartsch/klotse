"""#501 table widths: the port forward panel. A user widens column 0, then a second forward starts (an update of the table).
Before (master): the width is reset. After: the width is kept. Fake kubectl (sleeps), fake data, offscreen (no text drawn).
  QT_QPA_PLATFORM=offscreen PYTHONPATH=<tree>/src python docs/ux-review/501/capture_table_widths.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.port_forward import PortForwardPanel, PortForwards

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
SLEEP = "import time\ntime.sleep(30)\n"
manager = PortForwards(base=lambda context, kubeconfig: [sys.executable, "-u", "-c", SLEEP])
panel = PortForwardPanel(manager)
panel.resize(900, 220)
panel.show()
manager.start("prod", "shop", "Pod", "web-1", 80)
app.processEvents()
panel.table.setColumnWidth(0, 333)
manager.start("prod", "shop", "Pod", "web-2", 81)
app.processEvents()
print(mode, "column 0 width:", panel.table.columnWidth(0))
panel.grab().save(out)
manager.stop_all()
for fw in manager.items:
    if fw.process is not None:
        fw.process.kill()
        fw.process.waitForFinished(3000)
