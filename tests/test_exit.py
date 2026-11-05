import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import Qt

from klotse.ui.main_window import MainWindow


class EmptyService:
    def load_contexts(self):
        return [], None


def test_exit_action_closes_window_and_shutdown_is_idempotent(qtbot, monkeypatch):
    import klotse.ui.main_window as module

    stopped = []
    monkeypatch.setattr(module, "shutdown_all", lambda: stopped.append(True))
    win = MainWindow(EmptyService)
    qtbot.addWidget(win)
    win.show()
    assert not hasattr(win, "exit_toolbar")  # no Exit button on the right; File > Exit and the quit shortcut remain
    assert win.exit_action.text() == "Exit"
    closing = []
    win.closing.connect(lambda: closing.append(True))
    win.exit_action.trigger()
    assert not win.isVisible()
    assert closing == [True]
    assert stopped == [True]
    win.shutdown()
    assert stopped == [True]


@pytest.mark.parametrize("quit_method", ["menu", "native"])
def test_process_exits_with_stuck_worker(quit_method):
    # Exercise the actual entry point in a child: the fallback must never kill pytest.
    script = '''
import threading
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import QApplication
import klotse.__main__ as entry
from klotse.workers import Worker

class EmptyService:
    def load_contexts(self):
        return [], None

entry.KubeService = EmptyService
started = threading.Event()
blocked = threading.Event()
def stuck():
    started.set()
    blocked.wait()
worker = Worker(stuck)
QThreadPool.globalInstance().start(worker)
assert started.wait(2)
def quit_app():
    app = QApplication.instance()
    if METHOD == "native":
        app.quit()
    else:
        next(w for w in app.topLevelWidgets() if hasattr(w, "exit_action")).exit_action.trigger()
original_window = entry.MainWindow
def create_window(factory):
    win = original_window(factory)
    QTimer.singleShot(100, quit_app)
    return win
entry.MainWindow = create_window
entry.main()
'''
    script = script.replace('METHOD', repr(quit_method))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
