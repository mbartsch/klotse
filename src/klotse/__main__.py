import os
import sys
from threading import Timer

from PySide6.QtWidgets import QApplication

from .k8s.client import KubeService
from .ui import branding, theme
from .ui.main_window import MainWindow


def main():
    branding.set_windows_app_id()
    app = QApplication(sys.argv)
    theme.apply(app)
    app.setApplicationName("Klotse")
    app.setWindowIcon(branding.app_icon())
    win = MainWindow(KubeService)  # a factory: each open cluster gets its own connection
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
