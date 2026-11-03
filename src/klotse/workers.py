from PySide6.QtCore import QObject, QRunnable, Signal


class _Signals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    """Runs fn(*args) on the thread pool; emits result or error back to the GUI thread."""

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            try:
                res = self.fn(*self.args)
            except Exception as e:  # surfaced to the UI, not swallowed
                self.signals.error.emit(f"{type(e).__name__}: {e}")
            else:
                self.signals.result.emit(res)
        except RuntimeError:
            pass  # the window was closed while this call was in flight; nobody is left to receive the result
