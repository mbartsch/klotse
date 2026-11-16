"""#498 navigator state marks: one cluster in each state, one read-only, with fake names (no kubeconfig).
Before (master): dots in four colours only. After: a shape per state and a lock for read-only.
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/498/capture_navigator.py OUT.png"""
import sys

from PySide6.QtWidgets import QApplication

from klotse.ui.navigator import Navigator

app = QApplication.instance() or QApplication([])
nav = Navigator()
nav.resize(340, 300)
nav.set_contexts(["dev", "staging", "prod-eu", "prod-us"], aliases={"prod-eu": "EU production"})
nav.set_state("dev", "connected")
nav.set_state("staging", "connecting")
nav.set_state("prod-us", "failed", "unreachable")
nav.set_state("prod-eu", "connected")
if hasattr(nav, "set_read_only"):
    nav.set_read_only("prod-eu", True)
nav.show()
app.processEvents()
nav.grab().save(sys.argv[1])
print("saved", sys.argv[1])
