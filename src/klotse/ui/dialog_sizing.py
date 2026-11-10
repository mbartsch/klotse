"""Screen-aware initial sizing for resizable dialogs."""


def bounded_size(width, height, available_width, available_height, margin=32):
    """Keep a requested initial size within a screen's usable area."""
    max_width = max(1, int(available_width) - int(margin))
    max_height = max(1, int(available_height) - int(margin))
    return min(max(1, int(width)), max_width), min(max(1, int(height)), max_height)


def resize_dialog(dialog, width, height):
    """Apply a dialog's preferred initial size, bounded to the current screen.

    Qt screen geometry is expressed in device independent pixels, so the same
    sizing works on high-DPI displays. The helper does not fix the size; users
    can still resize the dialog after it opens.
    """
    width, height = int(width), int(height)
    if height <= 0:
        height = max(480, int(dialog.sizeHint().height()))
    screen = dialog.screen()
    if screen is None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
    if screen is not None:
        area = screen.availableGeometry()
        width, height = bounded_size(width, height, area.width(), area.height())
    dialog.resize(width, height)
    return width, height
