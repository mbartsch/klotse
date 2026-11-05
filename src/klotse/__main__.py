import os
import sys
from threading import Timer
from time import monotonic

from PySide6.QtWidgets import QApplication

from . import cli
from .k8s.client import KubeService
from .ui import branding, environment, log_view, theme, watcher
from .ui.settings_store import default_store
from .ui.main_window import MainWindow

THREAD_GRACE_MS = 1000  # how long a thread that is winding down may take before the process stops waiting for it


def leave(status, threads=None, hard_exit=os._exit, grace_ms=THREAD_GRACE_MS):
    """End the process once the event loop has returned.

    A QThread that is still running when Python tears down is destroyed mid-run: Qt prints "QThread: Destroyed while
    thread is still running" and aborts (a crash report on macOS). Watch and log threads sit in network reads that
    cannot be interrupted, so they may outlive the window by up to the server-side timeout. Give them a moment
    to finish; if one is still blocked, skip the interpreter teardown and end the process directly."""
    pending = list(threads) if threads is not None else [*watcher.running(), *log_view.running_streamers()]
    deadline = monotonic() + grace_ms / 1000
    for thread in pending:
        thread.wait(max(0, int((deadline - monotonic()) * 1000)))
    if not any(thread.isRunning() for thread in pending):
        sys.exit(status)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass  # a packaged Windows app has no console streams
    hard_exit(status)


def main(argv=None):
    status = cli.handle(sys.argv[1:] if argv is None else list(argv))  # --version, --self-test, --terminal-session
    if status is not None:
        sys.exit(status)
    environment.prepare_environment()  # macOS: make kubectl and auth helpers findable when started from Finder
    branding.set_windows_app_id()
    branding.set_macos_app_name()  # menu bar says Klotse, not Python, when run through the interpreter
    app = QApplication(sys.argv)
    theme.apply(app, theme.ThemeSettings(default_store()).selected())
    app.setApplicationName("Klotse")
    app.setDesktopFileName("klotse")  # Wayland matches the window to klotse.desktop (see desktop_entry.py)
    app.setWindowIcon(branding.app_icon())
    win = MainWindow(KubeService)  # a factory: each open cluster gets its own connection
    win.restore_clusters()  # only the clusters the user asked to remember when exiting; otherwise a clean list
    exit_timer = None

    def bound_shutdown():
        nonlocal exit_timer
        if exit_timer is None:
            # Qt waits for running QRunnables during teardown. A blocked API or
            # credential helper cannot be safely interrupted; bound process exit.
            exit_timer = Timer(3.0, lambda: os._exit(0))
            exit_timer.daemon = True
            exit_timer.start()

    win.closing.connect(bound_shutdown)
    # Also cover native application Quit, which may bypass the window close event.
    app.aboutToQuit.connect(win.shutdown)
    win.show()
    leave(app.exec())


if __name__ == "__main__":
    main()
