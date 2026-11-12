"""The cluster switcher (the drop-down above the cluster tabs) shows each open cluster's alias, and selecting an item still opens that cluster."""
from PySide6.QtCore import QSettings

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService, open_active


def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def test_the_switcher_shows_the_alias_and_selecting_it_opens_the_cluster(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    context = win.clusters.currentWidget().context
    win.aliases.set(context, "Production EU")
    win._sync_cluster_picker()
    assert win.cluster_picker.itemText(0) == "Production EU" and win.cluster_picker.itemData(0) == context
    assert win.cluster_picker.currentText() == "Production EU"
    win._pick_cluster(0)
    assert win.clusters.currentWidget().context == context
