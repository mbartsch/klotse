from threading import Event

from PySide6.QtCore import QThread, Signal

_RUNNING = set()  # a QThread must not be destroyed while it runs; keep each one until it has finished


class ChangeWatcher(QThread):
    """Runs a blocking change stream (KubeService.watch_changes) and emits `changed` for every event.

    stop() only sets a flag: the stream notices at its next event or when its server-side timeout (about 20 s)
    ends, and the thread then finishes on its own. Nothing ever waits for it on the GUI thread."""

    changed = Signal()

    def __init__(self, stream, parent=None):
        super().__init__(parent)
        self._stream = stream  # callable(stop_event) -> iterator
        self._stop = Event()
        _RUNNING.add(self)
        self.finished.connect(lambda: _RUNNING.discard(self))

    def run(self):
        try:
            for _ in self._stream(self._stop):
                if self._stop.is_set():
                    break
                self.changed.emit()
        except Exception:
            pass  # a broken stream just stops; the periodic reload still keeps the view current

    def stop(self):
        self._stop.set()
        try:
            self.changed.disconnect()
        except (RuntimeError, TypeError):
            pass
