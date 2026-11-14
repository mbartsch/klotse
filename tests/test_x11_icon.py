"""The X11 WM_HINTS icon hint (for MWM's minimized icon). Only X11 changes anything; the offscreen tests run elsewhere, so the
Xlib call itself is not exercised here: it is checked on a real X11 session (MWM) by hand."""

from PySide6.QtGui import QPixmap

from klotse.ui import x11_icon


def test_nothing_changes_off_x11(qtbot):
    pixmap = QPixmap(16, 16)
    pixmap.fill()
    assert x11_icon.set_wm_icon_hint(None, pixmap) is False  # no window
    assert x11_icon.set_wm_icon_hint(object(), QPixmap()) is False  # no pixmap to show


def test_the_offscreen_platform_is_not_x11(qtbot):
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.instance().platformName() != "xcb"


def test_the_hints_struct_has_the_xlib_field_order():
    names = [name for name, _ in x11_icon.XWMHints._fields_]
    assert names == ["flags", "input", "initial_state", "icon_pixmap", "icon_window", "icon_x", "icon_y", "icon_mask", "window_group"]
    assert x11_icon.ICON_PIXMAP_HINT == 4
