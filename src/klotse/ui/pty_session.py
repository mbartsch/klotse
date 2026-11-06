"""A program running in a pseudo-terminal, for the embedded terminal. Windows uses pywinpty (ConPTY, MIT); macOS and Linux use the
standard `pty` module. No Qt here: output and the end of the program come back through two callbacks, called from the reader
thread. `argv` is always a list (never a shell string), so nothing is re-parsed on the way."""
import os
import signal
import sys
import threading
import time

READ_SIZE = 65536
KILL_AFTER_S = 3.0  # POSIX: SIGTERM first, SIGKILL if the program is still there after this long


class PtySession:
    def __init__(self, argv, cols=100, rows=28, env=None):
        self.argv = list(argv)
        self.cols, self.rows = int(cols), int(rows)
        self.env = dict(env if env is not None else os.environ)
        self.on_output = lambda data: None  # bytes, from the reader thread
        self.on_exit = lambda: None  # once, from the reader thread
        self.running = False
        self._exit_called = False
        self._lock = threading.Lock()
        self._process = None  # winpty.PtyProcess
        self._pid = -1
        self._fd = -1

    # ---- start ------------------------------------------------------------------------------------------------
    def start(self):
        """Start the program. Raises OSError (or whatever the platform layer raises) when it cannot."""
        if not self.argv:
            raise OSError("no command to run")
        if sys.platform == "win32":
            self._start_windows()
        else:
            self._start_posix()
        self.running = True
        threading.Thread(name="Klotse terminal reader", target=self._read_loop, daemon=True).start()

    def _start_windows(self):
        import winpty
        env = {k: str(v) for k, v in self.env.items()}
        try:
            self._process = winpty.PtyProcess.spawn(self.argv, dimensions=(self.rows, self.cols), env=env)
        except Exception:  # noqa: BLE001 - ConPTY not available (very old Windows): the older winpty agent
            self._process = winpty.PtyProcess.spawn(self.argv, dimensions=(self.rows, self.cols), env=env, backend=1)

    def _start_posix(self):
        import fcntl
        import pty
        import struct
        import termios
        env = dict(self.env)
        env.setdefault("TERM", "xterm-256color")
        env["COLUMNS"], env["LINES"] = str(self.cols), str(self.rows)
        pid, fd = pty.fork()
        if pid == 0:  # the child: become the program; never return into the GUI process
            try:
                os.execvpe(self.argv[0], self.argv, env)
            except OSError as e:
                os.write(2, f"Cannot run {self.argv[0]}: {e}\r\n".encode("utf-8", "replace"))
            os._exit(127)
        self._pid, self._fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    # ---- reading ----------------------------------------------------------------------------------------------
    def _read_loop(self):
        try:
            while self.running:
                if sys.platform == "win32":
                    text = self._process.read(READ_SIZE)
                    data = text.encode("utf-8", "replace") if isinstance(text, str) else text
                else:
                    data = os.read(self._fd, READ_SIZE)
                if not data:
                    if sys.platform == "win32":
                        continue
                    break
                self.on_output(data)
        except (EOFError, OSError):
            pass  # the program ended
        finally:
            self._finish()

    def _finish(self):
        with self._lock:
            self.running = False
            if self._exit_called:
                return
            self._exit_called = True
        if sys.platform != "win32":
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                os.waitpid(self._pid, 0)  # reap it, so no zombie is left
            except (ChildProcessError, OSError):
                pass
        self.on_exit()

    # ---- input, size, stop ------------------------------------------------------------------------------------
    def write(self, data):
        """Send keys to the program (bytes or str)."""
        if not self.running:
            return
        raw = data.encode("utf-8") if isinstance(data, str) else data
        try:
            if sys.platform == "win32":
                self._process.write(raw.decode("utf-8", "replace"))
            else:
                os.write(self._fd, raw)
        except (OSError, EOFError):
            pass

    def resize(self, cols, rows):
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        if not self.running:
            return
        try:
            if sys.platform == "win32":
                self._process.setwinsize(self.rows, self.cols)
            else:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
        except (OSError, EOFError, ValueError):
            pass

    def stop(self):
        """End the program: the process itself is stopped, so no kubectl outlives its tab."""
        if not self.running:
            return
        if sys.platform == "win32":
            try:
                self._process.terminate(force=True)
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
            return
        try:
            os.kill(self._pid, signal.SIGTERM)
        except OSError:
            return

        def kill_later(pid=self._pid):
            time.sleep(KILL_AFTER_S)
            if not self.running:  # it ended and was reaped: the pid may belong to someone else by now
                return
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        threading.Thread(target=kill_later, daemon=True).start()
