"""Screen-aware initial sizing for resizable dialogs. A dialog's size is remembered per dialog class once the app's settings are
set (#501: they were lost on every start)."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

_settings = None  # the app's settings store (main window); None remembers nothing
SIZE_KEY = "dialogs/size/"


def use_settings(store):
    global _settings
    _settings = store


def _saved_size(dialog):
    if _settings is None:
        return None
    text = str(_settings.value(SIZE_KEY + dialog.__class__.__name__, "") or "")
    width, sep, height = text.partition("x")
    return (int(width), int(height)) if sep and width.isdigit() and height.isdigit() else None


class _SizeMemory(QObject):
    """Saves the dialog's size when it hides or closes."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def eventFilter(self, watched, event):
        if watched is self.dialog and event.type() in (QEvent.Hide, QEvent.Close) and _settings is not None:
            size = self.dialog.size()
            _settings.setValue(SIZE_KEY + self.dialog.__class__.__name__, f"{size.width()}x{size.height()}")
        return False


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
    saved = _saved_size(dialog)
    if saved is not None and screen is not None:
        width, height = bounded_size(saved[0], saved[1], area.width(), area.height())
    dialog.resize(width, height)
    if _settings is not None and isinstance(dialog, QWidget) and not hasattr(dialog, "_size_memory"):
        dialog._size_memory = _SizeMemory(dialog)
        dialog.installEventFilter(dialog._size_memory)
    return width, height
