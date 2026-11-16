"""#501 state dots: the navigator with one cluster in each state, under the Daylight theme. Before (master): the dots keep the
fixed colours of the default theme. After: the dots take the Daylight theme's colours. Windows platform, text drawn.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_state_colours.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.navigator import Navigator

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
theme.apply(app, theme.DAYLIGHT)
nav = Navigator()
nav.resize(260, 200)
nav.set_contexts(["idle-ctx", "connecting-ctx", "connected-ctx", "failed-ctx"])
nav.set_state("connecting-ctx", "connecting")
nav.set_state("connected-ctx", "connected")
nav.set_state("failed-ctx", "failed", "unreachable")
nav.show()
for _ in range(10):
    app.processEvents()
print(mode, "theme:", theme.current.name if hasattr(theme, "current") else "?")
nav.grab().save(out)
nav.close()
