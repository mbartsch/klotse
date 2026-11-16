"""#501 chart colours: the usage chart with four series under the Daylight (light) theme. Before (master): the bright series
colours. After: the darker set for light backgrounds. Windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_chart_palette.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.line_chart import LineChart

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
theme.apply(app, theme.DAYLIGHT)
chart = LineChart("Usage")
chart.resize(640, 360)
base = 1_700_000_000
series = {f"series-{i}": [(base + t * 60, 20 + 10 * i + (t * (i + 1)) % 17) for t in range(30)] for i in range(4)}
chart.set_data(series)
chart.show()
for _ in range(10):
    app.processEvents()
print(mode, "series colours:", [chart.colour(i).name() for i in range(4)])
chart.grab().save(out)
chart.close()
