import os
import subprocess
import sys
from pathlib import Path
from time import monotonic

import pytest

import klotse.__main__ as entry


class FakeThread:
    def __init__(self, finishes):
        self.finishes, self.running, self.waited = finishes, True, []

    def wait(self, ms):
        self.waited.append(ms)
        if self.finishes:
            self.running = False
        return not self.running

    def isRunning(self):
        return self.running


def test_leaving_with_nothing_running_is_a_normal_exit():
    hard = []
    with pytest.raises(SystemExit) as exit_info:
        entry.leave(3, threads=[], hard_exit=hard.append)
    assert exit_info.value.code == 3 and hard == []


def test_threads_that_finish_within_the_grace_period_allow_a_normal_exit():
    thread = FakeThread(finishes=True)
    hard = []
    with pytest.raises(SystemExit):
        entry.leave(0, threads=[thread], hard_exit=hard.append, grace_ms=500)
    assert hard == [] and thread.waited and thread.waited[0] <= 500


def test_a_thread_still_blocked_after_the_grace_period_skips_the_interpreter_teardown():
    stuck, hard = FakeThread(finishes=False), []
    entry.leave(7, threads=[stuck, FakeThread(finishes=True)], hard_exit=hard.append, grace_ms=50)
    assert hard == [7]  # os._exit in real life: it does not return, and sys.exit is not used


def run_entry_point(script, timeout=20):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    started = monotonic()
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=timeout)
    return result, monotonic() - started


def test_quitting_while_a_watch_thread_is_blocked_in_a_read_exits_cleanly():
    # Exercise the real entry point in a child: Python tearing down a running QThread is what Qt reports as
    # "QThread: Destroyed while thread is still running" and aborts (exit status 127 / a crash dialog on macOS).
    script = '''
import threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import klotse.__main__ as entry
from klotse.ui.watcher import ChangeWatcher

class EmptyService:
    def load_contexts(self):
        return [], None

entry.KubeService = EmptyService
blocked = threading.Event()
def stream(stop):
    blocked.wait(15)  # a watch stream sitting in a socket read until the server-side timeout
    return iter(())
keep = []
original_window = entry.MainWindow
def create_window(factory):
    win = original_window(factory)
    watcher = ChangeWatcher(stream)
    watcher.start()
    keep.append(watcher)
    QTimer.singleShot(200, lambda: QApplication.instance().quit())
    return win
entry.MainWindow = create_window
entry.main()
'''
    result, seconds = run_entry_point(script)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert "Destroyed while thread is still running" not in result.stderr
    assert seconds < 10  # it did not wait out the 15 s read
