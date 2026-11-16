"""#501: the main window remembers its size and place; its menus show their tooltips."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService


def window(tmp_path, qtbot):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    return win


def test_the_window_opens_where_it_was_last_closed(qtbot, tmp_path):
    first = window(tmp_path, qtbot)
    first.resize(600, 420)
    first.move(20, 30)
    first._remember_geometry()
    second = window(tmp_path, qtbot)
    assert second.width() == 600 and second.height() == 420


def test_without_a_saved_place_the_window_opens_at_its_default_size(qtbot, tmp_path):
    win = window(tmp_path, qtbot)
    assert win.width() == 1500 and win.height() == 850


def test_the_menus_show_their_tooltips(qtbot, tmp_path):
    win = window(tmp_path, qtbot)
    view = next(a.menu() for a in win.menuBar().actions() if a.text() == "&View")
    assert win.edit_menu.toolTipsVisible() and view.toolTipsVisible()
    navigate = next(a.menu() for a in view.actions() if a.text() == "&Navigate")
    assert navigate.toolTipsVisible()  # submenus too


def test_a_theme_change_restyles_the_navigator_headings(qtbot, monkeypatch):
    from klotse.ui import theme
    from klotse.ui.navigator import Navigator
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod"])
    header = nav.topLevelItem(0)
    monkeypatch.setattr(theme, "MUTED", "#123456")
    nav.refresh_theme()
    assert header.foreground(0).color().name() == "#123456"
