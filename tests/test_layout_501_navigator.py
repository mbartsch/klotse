"""#501: the navigator can be dragged wider than 360 px, so long alias rows are not cut off."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDockWidget

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService


def test_the_navigator_can_be_dragged_wider_than_before(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    nav_dock = win.findChild(QDockWidget, "clustersDock")
    assert nav_dock.minimumWidth() == 218 and nav_dock.maximumWidth() == 520
