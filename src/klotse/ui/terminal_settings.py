import sys

from PySide6.QtCore import QObject, QSettings
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from .settings_store import default_store


class TerminalSettings(QObject):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else default_store()
        saved = self.store.value("terminal/macos", "Terminal")
        self.mac_terminal = saved if saved in ("Terminal", "iTerm2") else "Terminal"
        # Where shells open: False = an external terminal (the default), True = a tab inside Klotse (xterm.js)
        self.embedded = str(self.store.value("terminal/embedded", "false")).lower() == "true"

    def set_embedded(self, on):
        self.store.setValue("terminal/embedded", "true" if on else "false")
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save terminal settings to {self.store.fileName()}")
        self.embedded = bool(on)

    def set_mac_terminal(self, terminal):
        if terminal not in ("Terminal", "iTerm2"):
            raise ValueError(f"Unsupported macOS terminal: {terminal}")
        self.store.setValue("terminal/macos", terminal)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save terminal settings to {self.store.fileName()}")
        self.mac_terminal = terminal


class TerminalPanel(QWidget):
    """The Terminal page of Preferences: whether shells open inside Klotse or in an external terminal, and (macOS) which app that
    is. `save()` writes the choices when Preferences is saved and raises ValueError/OSError if it cannot."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        form = QFormLayout(self)
        from . import embedded_terminal
        hint = QLabel("Pod shells, attach sessions and node shells open in the place chosen here. Saved for all clusters and "
                      "future sessions. Inside Klotse, a session is its own window (not kept on top), with a button to dock it into the Klotse window, and it ends when you close it.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.where = QComboBox()
        self.where.addItems(["External terminal", "Inside Klotse"])
        self.where.setCurrentIndex(1 if settings.embedded else 0)
        form.addRow("Open shells in", self.where)
        self.where_note = QLabel("" if embedded_terminal.available() else
                                 "The embedded terminal (QtWebEngine) cannot start here, so an external terminal is used.")
        self.where_note.setWordWrap(True)
        self.where_note.setVisible(bool(self.where_note.text()))
        form.addRow(self.where_note)
        self.mac_hint = QLabel("External terminal on macOS: Terminal or iTerm2 (must be installed). Successful sessions close "
                               "automatically; errors stay visible until Enter is pressed.")
        self.mac_hint.setWordWrap(True)
        self.terminal = QComboBox()
        self.terminal.addItems(["Terminal", "iTerm2"])
        self.terminal.setCurrentText(settings.mac_terminal)
        form.addRow(self.mac_hint)
        form.addRow("macOS terminal", self.terminal)
        if sys.platform != "darwin":
            for widget in (self.mac_hint, self.terminal):
                widget.hide()
            form.labelForField(self.terminal).hide()

    def save(self):
        self.settings.set_mac_terminal(self.terminal.currentText())
        self.settings.set_embedded(self.where.currentIndex() == 1)
