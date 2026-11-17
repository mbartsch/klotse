"""Native Windows display capture; run with PYTHONPATH=<checkout>/src and label output dir."""
import os
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from klotse.ui import theme
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow

class Service:
    @staticmethod
    def load_contexts():
        return [], None

app = QApplication([])
assert app.platformName() == 'windows'
theme.apply(app, theme.HARBOUR, QFont('Consolas', 9))
with tempfile.TemporaryDirectory() as folder:
    win = MainWindow(Service, label_settings=LabelSettings(QSettings(str(Path(folder) / 'settings.ini'), QSettings.IniFormat)))
    win.resize(1000, 650)
    win.move(100, 100)
    win.show()
    win.raise_()
    win.activateWindow()
    QTest.qWait(400)
    bar = win.menuBar()
    action = bar.actions()[0]
    point = bar.mapToGlobal(bar.actionGeometry(action).center())
    target = app.widgetAt(point)
    print(sys.argv[1], 'mouse target:', type(target).__name__)
    QTest.mouseMove(target, target.mapFromGlobal(point))
    QTest.mouseClick(target, Qt.LeftButton, pos=target.mapFromGlobal(point))
    QTest.qWait(300)
    print('File popup visible:', action.menu().isVisible())
    rect = win.frameGeometry()
    screen = win.screen()
    out = Path(sys.argv[2]) / (sys.argv[1] + '.png')
    assert win.grab().save(str(out))
    if action.menu().isVisible():
        assert action.menu().grab().save(str(Path(sys.argv[2]) / 'after-file-popup.png'))
    action.menu().hide()
    win.close()
