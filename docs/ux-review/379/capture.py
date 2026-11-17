"""Capture the source dialog on the native Windows display with synthetic data."""
import os
import sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from klotse.ui import theme
from klotse.ui.metrics_preferences import AddSourceDialog
app = QApplication([])
assert app.platformName() == 'windows'
theme.apply(app, theme.HARBOUR, QFont('Segoe UI', 9))
dialog = AddSourceDialog(['demo-cluster'], service='monitoring/prometheus:9090',
                         discover=lambda name, done: done(['monitoring/prometheus:9090']))
dialog.resize(740, 590)
dialog.show()
if hasattr(dialog, 'tester'):
    dialog.tester = lambda context, source, done, auth: done('Demo response: Prometheus API answered. No live cluster contacted.')
    dialog.test_button.click()
QTest.qWait(350)
assert dialog.grab().save(str(Path(sys.argv[2]) / (sys.argv[1] + '.png')))
dialog.close()
