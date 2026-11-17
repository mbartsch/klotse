"""Capture Preferences > General before and after #774 on Windows Qt.

Usage: QT_QPA_PLATFORM=windows PYTHONPATH=src python capture_preferences_fonts.py before|after OUT.png
"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog


mode, output = sys.argv[1:3]
app = QApplication.instance() or QApplication([])
theme.apply(app, theme.HARBOUR)
with tempfile.TemporaryDirectory(prefix="klotse-font-capture-") as folder:
    store = QSettings(str(Path(folder) / "Klotse.ini"), QSettings.IniFormat)
    kwargs = {"themes": theme.ThemeSettings(store)}
    if mode == "after":
        kwargs["fonts"] = theme.FontSettings(store)
    dialog = PreferencesDialog(KubeconfigSettings(store), Path(folder) / "config", **kwargs)
    dialog.resize(780, 560)
    dialog.show()
    for _ in range(5):
        app.processEvents()
    if not dialog.grab().save(output):
        raise RuntimeError(f"Could not save {output}")
    dialog.close()
print("saved", output)
