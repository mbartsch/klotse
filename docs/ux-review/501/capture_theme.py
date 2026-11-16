"""#501 theme change: the navigator's headings take the new colour at once. Before (master): the headings keep the colour they
were made with until a restart; after: they follow the change. Fake clusters only.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/501/capture_theme.py before|after OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.navigator import Navigator

mode, out = sys.argv[1], sys.argv[2]
app = QApplication.instance() or QApplication([])
nav = Navigator()
nav.resize(300, 160)
nav.set_contexts(["dev", "prod-eu"])
nav.set_state("dev", "connected")
nav.show()
theme.MUTED = "#e0a030"  # a theme change: the header colour is now amber
if mode == "after" and hasattr(nav, "refresh_theme"):
    nav.refresh_theme()
app.processEvents()
nav.grab().save(out)
print("saved", out)
