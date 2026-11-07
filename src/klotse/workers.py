import time

from PySide6.QtCore import QObject, QRunnable, Signal

from .k8s.timing import name_of, size_of, timings


class _Signals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    """Runs fn(*args) on the thread pool; emits result or error back to the GUI thread."""

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.submitted = None  # perf_counter() when it was queued (set by the workspace; only used by the timing log)
        self.context = None
        self.signals = _Signals()

    def run(self):
        started = time.perf_counter()
        try:
            try:
                res = self.fn(*self.args)
            except Exception as e:  # surfaced to the UI, not swallowed
                self._log(started, None, type(e).__name__)
                self.signals.error.emit(f"{type(e).__name__}: {e}")
            else:
                self._log(started, res, "")
                self.signals.result.emit(res)
        except RuntimeError:
            pass  # the window was closed while this call was in flight; nobody is left to receive the result

    def _log(self, started, result, error):
        if timings.enabled:
            queued = round(started - self.submitted, 3) if self.submitted is not None else None
            timings.record("worker", name_of(self.fn), time.perf_counter() - started, queued=queued, items=size_of(result),
                           error=error or None, cluster=self.context)
