"""#501: the cluster tab bar stays visible with one cluster; the about and regex-error texts use the theme's colours."""
from PySide6.QtCore import QSettings

from klotse.ui import theme
from klotse.ui.about import AboutDialog
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService, open_active
from test_group_regex import dialog_for


def window(tmp_path, qtbot):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    return win


def test_the_tab_bar_stays_visible_with_one_cluster_open(qtbot, tmp_path):
    win = window(tmp_path, qtbot)
    open_active(win)
    assert win.clusters.count() == 1
    assert not win.clusters.tabBarAutoHide()


def test_the_about_trademark_note_uses_the_theme_muted_colour(qtbot):
    dialog = AboutDialog()
    qtbot.addWidget(dialog)
    labels = [label for label in dialog.findChildren(type(dialog.info)) if "Linux Foundation" in label.text()]
    assert labels and f"color: {theme.MUTED}" in labels[0].styleSheet()


def test_the_regex_error_uses_the_theme_error_colour(qtbot):
    dialog = dialog_for(qtbot)
    dialog.by_regex.setChecked(True)
    dialog.pattern.setText("([unclosed")
    assert dialog.regex_error.text().startswith("Invalid expression")
    assert f"color: {theme.BAD}" in dialog.regex_error.styleSheet()


def test_numbers_are_right_aligned_and_text_stays_left(qapp):
    from types import SimpleNamespace

    from PySide6.QtCore import Qt

    from klotse.ui.table_model import TableModel
    model = TableModel([("Name", "name"), ("Restarts", "restarts")])
    model.set_rows([SimpleNamespace(name="web-1", restarts=3), SimpleNamespace(name="web-2", restarts="none")])
    assert model.index(0, 1).data(Qt.TextAlignmentRole) == (Qt.AlignRight | Qt.AlignVCenter)
    assert model.index(0, 0).data(Qt.TextAlignmentRole) is None
    assert model.index(1, 1).data(Qt.TextAlignmentRole) is None
