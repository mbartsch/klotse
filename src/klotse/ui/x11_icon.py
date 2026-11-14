"""The classic X11 icon hint (the WM_HINTS icon pixmap) for window managers that do not read _NET_WM_ICON, such as MWM, which shows
it for a minimized window. Qt's X11 (xcb) backend sets only _NET_WM_ICON. This sets the classic hint as well, through Xlib, on the
native window. Only on X11; on other platforms, or when Xlib cannot be used, nothing changes and the Qt icon stays."""
import ctypes
import ctypes.util

from PySide6.QtGui import QGuiApplication

ICON_PIXMAP_HINT = 1 << 2  # Xutil.h: IconPixmapHint


class XWMHints(ctypes.Structure):
    """Xlib's XWMHints (Xutil.h), laid out the same way for the C library."""
    _fields_ = [("flags", ctypes.c_long), ("input", ctypes.c_int), ("initial_state", ctypes.c_int),
                ("icon_pixmap", ctypes.c_ulong), ("icon_window", ctypes.c_ulong), ("icon_x", ctypes.c_int),
                ("icon_y", ctypes.c_int), ("icon_mask", ctypes.c_ulong), ("window_group", ctypes.c_ulong)]


_KEEP = []  # the QPixmap must outlive the X11 pixmap that Qt created for it


def set_wm_icon_hint(window, pixmap):
    """Set the WM_HINTS icon pixmap of the shown `window` (a QWindow) to `pixmap` (a QPixmap). Returns True when it was set."""
    app = QGuiApplication.instance()
    if app is None or app.platformName() != "xcb" or window is None or pixmap is None or pixmap.isNull():
        return False
    try:
        xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        display = app.platformNativeInterface().nativeResourceForIntegration(b"display")
        handle = pixmap.handle()
    except (OSError, AttributeError, TypeError):
        return False
    if not display or not handle:
        return False
    xlib.XSetWMHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWMHints)]
    xlib.XSetWMHints.restype = ctypes.c_int
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    hints = XWMHints(flags=ICON_PIXMAP_HINT, icon_pixmap=int(handle))
    _KEEP[:] = [pixmap]
    xlib.XSetWMHints(ctypes.c_void_p(int(display)), ctypes.c_ulong(int(window.winId())), ctypes.byref(hints))
    xlib.XFlush(ctypes.c_void_p(int(display)))
    return True
