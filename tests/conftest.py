import faulthandler
import tempfile
import threading
import time

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


# ---- what the background workers did, printed when a test fails -----------------------------------------------------
# Waits for a worker's answer time out now and then on the macOS runners and nothing in the failure says why. This records, per
# test, every runnable handed to the pool and how far each worker got, so a failure shows whether the worker never started, is
# still inside its function, could not emit its signal (both worker classes swallow RuntimeError there), or emitted a signal
# that never reached the GUI thread.
_TRACE = {"began": 0.0, "queued": [], "runs": []}


def _since():
    return round(time.monotonic() - _TRACE["began"], 3)


class _EmitProbe:
    def __init__(self, signal, name, record):
        self.signal, self.name, self.record = signal, name, record

    def emit(self, *args):
        try:
            self.signal.emit(*args)
        except BaseException as problem:
            self.record[f"emit {self.name}"] = f"RAISED {problem!r} at {_since()}"
            raise
        self.record[f"emit {self.name}"] = f"ok at {_since()}"


class _SignalsProbe:
    def __init__(self, signals, record):
        self.signals, self.record = signals, record

    def __getattr__(self, name):
        return _EmitProbe(getattr(self.signals, name), name, self.record)


def _traced(original):
    def run(self):
        fn = getattr(self, "fn", None)
        record = {"worker": type(self).__name__, "fn": getattr(fn, "__qualname__", ""), "thread": threading.get_ident(),
                  "started": _since()}
        _TRACE["runs"].append(record)
        signals = self.signals
        self.signals = _SignalsProbe(signals, record)
        try:
            original(self)
        except BaseException as problem:
            record["run"] = f"RAISED {problem!r}"
            raise
        finally:
            self.signals = signals
            record["ended"] = _since()
    return run


class _TracedPool(QThreadPool):
    def start(self, runnable, *args):
        _TRACE["queued"].append((type(runnable).__name__, _since(), threading.get_ident()))
        super().start(runnable, *args)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    lines = [f"now {_since()} s after the test began, GUI thread {threading.main_thread().ident}"]
    pool = getattr(item, "funcargs", {}).get("own_thread_pool")
    if pool is not None:
        lines.append(f"pool: {pool.activeThreadCount()} active of {pool.maxThreadCount()} threads")
    lines.append(f"queued ({len(_TRACE['queued'])}): {_TRACE['queued']}")
    lines.append(f"runs ({len(_TRACE['runs'])}):")
    lines += [f"  {record}" for record in _TRACE["runs"]]
    with tempfile.TemporaryFile("w+") as dump:
        faulthandler.dump_traceback(dump, all_threads=True)
        dump.seek(0)
        lines.append("threads:\n" + dump.read())
    report.sections.append(("background workers", "\n".join(lines)))


@pytest.fixture(autouse=True)
def own_thread_pool(monkeypatch):
    """The app runs its background work on Qt's global pool. Earlier tests can leave it full of workers that are still waiting
    (a macOS runner has only 3-4 threads), and the next test's worker then never gets a thread. Give every test a pool of its own."""
    from klotse.plugins.manager import _LoadPlugin
    from klotse.workers import Worker

    _TRACE.update(began=time.monotonic(), queued=[], runs=[])
    for kind in (Worker, _LoadPlugin):
        monkeypatch.setattr(kind, "run", _traced(kind.run))
    pool = _TracedPool()
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: pool))
    yield pool
    pool.clear()
    pool.waitForDone(10000)
