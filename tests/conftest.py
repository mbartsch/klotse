import tempfile

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QThreadPool
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


@pytest.fixture(autouse=True)
def own_thread_pool(monkeypatch):
    """The app runs its background work on Qt's global pool. Earlier tests can leave it full of workers that are still waiting
    (a macOS runner has only 3-4 threads), and the next test's worker then never gets a thread. Give every test a pool of its own."""
    pool = QThreadPool()
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: pool))
    yield pool
    pool.clear()
    pool.waitForDone(10000)
