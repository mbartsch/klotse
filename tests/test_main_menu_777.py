"""#777: the first click must open File and Edit after applying the configured font."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from klotse.ui import theme
from klotse.ui.main_window import MainWindow
from test_app import FakeService


def test_file_and_edit_open_on_first_click_with_configured_font(qapp, qtbot):
    font = QFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
    font.setPointSize(13)
    theme.apply(qapp, theme.HARBOUR, font)
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    win.show()
    qapp.processEvents()

    for action in win.menuBar().actions()[:2]:
        point = win.menuBar().mapToGlobal(win.menuBar().actionGeometry(action).center())
        assert qapp.widgetAt(point) is win.menuBar()
        QTest.mouseClick(win.menuBar(), Qt.LeftButton, pos=win.menuBar().actionGeometry(action).center())
        qapp.processEvents()
        assert action.menu().isVisible(), action.text()
        action.menu().hide()
