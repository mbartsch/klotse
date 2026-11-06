import tempfile

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtWidgets import QMessageBox

# QtWebEngine (the embedded terminal) needs this set before the QApplication that pytest-qt creates
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

# Tests must never read or write the user's real preferences: the default settings store lives in a throw-away folder
_SETTINGS_DIR = tempfile.mkdtemp(prefix="klotse-test-settings-")
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _SETTINGS_DIR)


@pytest.fixture(autouse=True)
def no_blocking_exit_question(monkeypatch):
    """Closing a shown MainWindow with clusters open asks about remembering them; a modal question would hang a test
    that never expects it. Tests that care patch QMessageBox.question themselves, which replaces this."""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
