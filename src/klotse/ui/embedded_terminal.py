"""Shell and attach sessions inside the Klotse window, using the termqt terminal widget (LGPL-2.1+, a pure Python Qt widget; on
Windows its pseudo-terminal is pywinpty/ConPTY, elsewhere the standard `pty` module). A session is a tab in the Logs & activity dock
running the same explicit-context kubectl command the external terminals run. termqt is optional at run time: without it (or when
the pseudo-terminal cannot start) `ClusterWorkspace` opens the external terminal as before."""
import os
import shlex
import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from . import theme

os.environ.setdefault("QT_API", "pyside6")  # termqt reaches Qt through qtpy, which must not pick another binding


def available():
    """True when termqt (and, on Windows, pywinpty) can be imported."""
    try:
        import termqt  # noqa: F401
        if sys.platform == "win32":
            import winpty  # noqa: F401
        return True
    except Exception:  # an import error, or a binding qtpy cannot use; never fatal
        return False


def command_text(argv):
    """argv as the one command string termqt's POSIX pseudo-terminal takes (it splits it again with shlex)."""
    return shlex.join(argv)


def make_io(argv, cols, rows):
    """The termqt pseudo-terminal that will run `argv`. termqt's own POSIX class has two slips (`is_alive` misspells `self.pid`,
    and the kill timer sleeps 3000 *seconds*), so a small subclass fixes them; its Windows exec class is broken, so the base is used."""
    if sys.platform == "win32":
        from termqt import TerminalWinptyIO

        class WinIO(TerminalWinptyIO):
            def _read_loop(self):
                try:  # winpty raises EOFError when the program ends; termqt only has a `finally` there, so it printed a traceback
                    super()._read_loop()
                except EOFError:
                    pass
        return WinIO(cols, rows, list(argv))  # winpty mangles a quoted string but takes an argv list as it is
    from termqt import TerminalPOSIXExecIO

    class PosixIO(TerminalPOSIXExecIO):
        def is_alive(self):
            try:
                os.kill(self.pid, 0)
                return True
            except OSError:
                self.running = False
                return False

        def terminate(self):
            if self.running:
                import signal
                import threading
                import time
                self.running = False
                try:
                    os.kill(self.pid, signal.SIGTERM)
                except OSError:
                    return

                def _kill_later():
                    time.sleep(3)
                    if self.is_alive():
                        try:
                            os.kill(self.pid, signal.SIGKILL)
                        except OSError:
                            pass
                threading.Thread(target=_kill_later, daemon=True).start()

    return PosixIO(cols, rows, command_text(argv), env=dict(os.environ))


class TerminalTab(QWidget):
    """One session. `key` marks it for the dock; closing the tab calls `stop()`, which ends the process (and so no kubectl outlives
    its tab). `io_factory(argv, cols, rows)` is replaceable for tests. `finished` fires once, when the process has ended."""
    finished = Signal()
    _reader_done = Signal()
    COLS, ROWS = 100, 28

    def __init__(self, title, argv, io_factory=make_io, parent=None):
        super().__init__(parent)
        from termqt import Terminal
        self.key = f"terminal:{title}"
        self.title = title
        self.argv = list(argv)
        self.ended = False
        self.terminal = Terminal(640, 380, logger=None)
        self.status = QLabel()
        theme.muted(self.status)
        self.status.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.terminal, 1)
        layout.addWidget(self.status)
        self.io = io_factory(self.argv, self.COLS, self.ROWS)
        self.terminal.stdin_callback = self.io.write
        self.terminal.resize_callback = self.io.resize
        self.io.stdout_callback = self.terminal.stdout
        self.io.terminated_callback = self._process_ended  # called from the reader thread
        self._reader_done.connect(self._show_ended)  # emitted from the reader thread, delivered on the GUI thread

    def screen_text(self):
        """Everything the terminal shows or has scrolled back, as text (for tests and a future Copy all)."""
        return self.terminal._get_all_text_rstrip()

    def start(self):
        """Start the process; False (and the tab says why) when the pseudo-terminal cannot be created."""
        try:
            self.io.spawn()
        except Exception as e:  # noqa: BLE001 - whatever the platform's pty layer raises
            self._ended(f"Could not start the session: {e}")
            return False
        if not getattr(self.io, "running", True):
            self._ended("Could not start the session.")
            return False
        self.terminal.setFocus()
        return True

    def _process_ended(self):
        self._reader_done.emit()

    def _show_ended(self):
        self._ended("Session ended. Close this tab.")
        self.finished.emit()

    def _ended(self, text):
        self.ended = True
        self.status.setText(text)
        self.status.show()

    def stop(self):
        """Called when the tab is closed or the cluster is: end the process."""
        try:
            self.io.terminate()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        self.ended = True
