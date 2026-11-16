"""#501: the cluster navigator can be hidden and shown from View > Show clusters panel."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDockWidget

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService


def test_the_clusters_panel_hides_and_shows_from_the_view_menu(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    nav_dock = win.findChild(QDockWidget, "clustersDock")
    assert win.clusters_panel_action.isChecked() and not nav_dock.isHidden()
    win.clusters_panel_action.setChecked(False)
    assert nav_dock.isHidden()
    win.clusters_panel_action.setChecked(True)
    assert not nav_dock.isHidden()
