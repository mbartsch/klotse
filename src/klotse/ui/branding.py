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


def set_macos_app_name(name="Klotse"):
    """Run through the Python interpreter (pipx, run.sh, python -m klotse), macOS names the menu bar after the
    interpreter's own bundle: "Python". Rename the running process before the menus are built. A packaged
    Klotse.app already has the right name in its Info.plist, so this only acts on a source or pip install.
    Returns True when the name was set; never raises."""
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return False
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))  # makes NSBundle and NSString available
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(receiver, selector, *args):  # objc_msgSend with a signature that fits this call
            kinds = [ctypes.c_char_p if isinstance(a, ctypes.c_char_p) else ctypes.c_void_p for a in args]
            call = ctypes.cast(objc.objc_msgSend, ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *kinds))
            return call(receiver, objc.sel_registerName(selector), *args)

        def nsstring(text):
            return send(objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", ctypes.c_char_p(text.encode()))

        bundle = send(objc.objc_getClass(b"NSBundle"), b"mainBundle")
        info = send(bundle, b"infoDictionary") if bundle else None
        if not info:
            return False
        send(info, b"setObject:forKey:", nsstring(name), nsstring("CFBundleName"))
        send(send(objc.objc_getClass(b"NSProcessInfo"), b"processInfo"), b"setProcessName:", nsstring(name))
        return True
    except Exception:  # a cosmetic nicety must never stop the app from starting
        return False


def set_windows_app_id():
    """Give the process its own Windows identity so the taskbar shows Klotse's icon, not pythonw's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Klotse.Klotse")
    except (AttributeError, OSError):
        pass
