import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QIcon, QPixmap

ASSETS = Path(__file__).resolve().parent.parent / "assets"
# First one found is used. A PNG is preferred because it can carry transparency.
LOGO_NAMES = ("klotse_logo.png", "logo.png", "klotse_logo.jpg", "klotse_logo.jpeg", "logo.jpg", "logo.jpeg")


def logo_path():
    """The logo file shipped in klotse/assets, or None when it has not been added."""
    return next((ASSETS / name for name in LOGO_NAMES if (ASSETS / name).is_file()), None)


def square_logo(size=None):
    """The logo cropped to a centered square (the artwork sits in the middle of a wide canvas), optionally scaled
    down to at most `size` pixels. Returns a null pixmap when there is no readable logo."""
    path = logo_path()
    pixmap = QPixmap(str(path)) if path else QPixmap()
    if pixmap.isNull():
        return QPixmap()
    side = min(pixmap.width(), pixmap.height())
    pixmap = pixmap.copy(QRect((pixmap.width() - side) // 2, (pixmap.height() - side) // 2, side, side))
    if size and side > size:
        pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pixmap


def app_icon():
    """The application / window icon (an empty icon when there is no logo)."""
    pixmap = square_logo(256)
    return QIcon(pixmap) if not pixmap.isNull() else QIcon()


def set_windows_app_id():
    """Give the process its own Windows identity so the taskbar shows Klotse's icon, not pythonw's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klotse.Klotse")
    except (AttributeError, OSError):
        pass
