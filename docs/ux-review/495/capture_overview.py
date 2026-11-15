import sys
from PySide6.QtWidgets import QApplication
from klotse.ui.overview import CLUSTER_CARDS, OverviewView
out = sys.argv[1]
app = QApplication.instance() or QApplication([])
view = OverviewView(CLUSTER_CARDS)
view.resize(700, 120)
view.show()
view.workloads_button.click()
app.processEvents()
view.grab().save(out)
print(out, "dashboard:", view.dashboard_button.isChecked(), "workloads:", view.workloads_button.isChecked())
