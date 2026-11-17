"""Native Windows plugin summary, with isolated settings and no cloud requests."""
import os
import sys
import tempfile
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from klotse.plugins.manager import PluginManager
from klotse.ui.plugins_panel import PluginsPanel
from klotse.ui import theme
app = QApplication([])
assert app.platformName() == 'windows'
theme.apply(app, theme.HARBOUR)
with tempfile.TemporaryDirectory() as folder:
    manager = PluginManager(QSettings(str(Path(folder) / 'settings.ini'), QSettings.IniFormat),
                            bundled=('klotse_plugins.gcp',), entry_points=False)
    panel = PluginsPanel(manager)
    panel.resize(1050, 730)
    panel.show()
    panel.list.setCurrentRow(0)
    QTest.qWait(350)
    assert panel.grab().save(str(Path(sys.argv[2]) / (sys.argv[1] + '.png')))
    panel.close()
