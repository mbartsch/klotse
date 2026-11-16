"""#501: Help > Keyboard shortcuts lists the shortcuts the menus actually have."""
from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.shortcuts_dialog import ShortcutsDialog, shortcut_rows
from test_app import FakeService


def test_the_shortcut_list_is_read_from_the_menus(qtbot, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    rows = shortcut_rows(win.menuBar())
    assert rows and all(keys and action for action, keys in rows)
    assert any(a.endswith("Preferences") and keys == win.preferences_action.shortcut().toString(QKeySequence.NativeText) for a, keys in rows)
    dialog = ShortcutsDialog(rows, win)
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == len(rows) and dialog.table.item(0, 0).text() == rows[0][0]
    monkeypatch.setattr(ShortcutsDialog, "exec", lambda self: 0)
    win.shortcuts_action.trigger()


def test_an_empty_list_says_so(qtbot):
    dialog = ShortcutsDialog([])
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 0
