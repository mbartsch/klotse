import os
import sys
from threading import Timer

from PySide6.QtWidgets import QApplication

from . import cli
from .k8s.client import KubeService
from .ui import branding, environment, theme
from .ui.main_window import MainWindow


def main(argv=None):
    status = cli.handle(sys.argv[1:] if argv is None else list(argv))  # --version, --self-test, --terminal-session
    if status is not None:
        sys.exit(status)
    environment.prepare_environment()  # macOS: make kubectl and auth helpers findable when started from Finder
    branding.set_windows_app_id()
    branding.set_macos_app_name()  # menu bar says Klotse, not Python, when run through the interpreter
    app = QApplication(sys.argv)
    theme.apply(app)
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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
