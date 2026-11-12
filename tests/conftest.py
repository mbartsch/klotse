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
_TRACE = {"began": 0.0, "queued": [], "runs": [], "status": [], "slow": None}


def pytest_collection_modifyitems(items):
    """Run the embedded terminal tests last: they are the only ones that start QtWebEngine (Chromium) in the test process. On the
    macOS runners, tests that ran after them now and then waited 5 s for a worker's answer that had been emitted: the trace showed the
    GUI thread (renamed CrBrowserMain by Chromium) delivering neither that queued signal nor a 1 s timer during the wait. The sort is
    stable, so every other test keeps its place."""
    items.sort(key=lambda item: item.path.name == "test_embedded_terminal.py")


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
        for name in ("result", "error", "finished"):  # connected after the app's own slot, so it runs after it on the receiving thread
            signal = getattr(signals, name, None)
            if signal is not None:
                signal.connect(lambda *_, name=name: record.__setitem__(
                    f"delivered {name}", f"at {_since()} on thread {threading.get_ident()}"))
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


def _status_traced(name, original):
    def call(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        finally:
            _TRACE["status"].append((_since(), self.kind, name, self.status.text()[:60]))
    return call


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
    lines.append(f"table status lines, last 30 of {len(_TRACE['status'])}: (seconds, kind, call, text after it)")
    lines += [f"  {entry}" for entry in _TRACE["status"][-30:]]
    slow = _TRACE["slow"]
    if slow is not None:
        faulthandler.cancel_dump_traceback_later()
        slow.seek(0)
        lines.append("every thread, each 2 s while the test ran (the last 6000 characters):\n" + slow.read()[-6000:])
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
    from klotse.ui.resource_table import ResourceTable
    from klotse.workers import Worker

    slow = tempfile.TemporaryFile("w+")
    _TRACE.update(began=time.monotonic(), queued=[], runs=[], status=[], slow=slow)
    faulthandler.dump_traceback_later(2, repeat=True, file=slow)  # where every thread is while a test takes long
    for kind in (Worker, _LoadPlugin):
        monkeypatch.setattr(kind, "run", _traced(kind.run))
    for name in ("set_loading", "set_error", "set_rows", "append_rows", "show_status", "_loading_tick"):
        monkeypatch.setattr(ResourceTable, name, _status_traced(name, getattr(ResourceTable, name)))
    pool = _TracedPool()
    monkeypatch.setattr(QThreadPool, "globalInstance", staticmethod(lambda: pool))
    yield pool
    faulthandler.cancel_dump_traceback_later()
    _TRACE["slow"] = None
    slow.close()
    pool.clear()
    pool.waitForDone(10000)
