from PySide6.QtCore import QObject, QSettings
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from .settings_store import default_store


class TerminalSettings(QObject):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else default_store()
        saved = self.store.value("terminal/macos", "Terminal")
        self.mac_terminal = saved if saved in ("Terminal", "iTerm2") else "Terminal"

    def set_mac_terminal(self, terminal):
        if terminal not in ("Terminal", "iTerm2"):
            raise ValueError(f"Unsupported macOS terminal: {terminal}")
        self.store.setValue("terminal/macos", terminal)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save terminal settings to {self.store.fileName()}")
        self.mac_terminal = terminal


class TerminalPanel(QWidget):
    """The Terminal page of Preferences (macOS): which app opens pod shells, attach sessions and node shells.
    `save()` writes the choice when Preferences is saved and raises ValueError/OSError if it cannot."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        form = QFormLayout(self)
        hint = QLabel("Choose the macOS app for pod shells, attach sessions and node shells.\n"
                      "Successful sessions close automatically. Errors stay visible until Enter is pressed.\n"
                      "iTerm2 must be installed. Saved for all clusters and future sessions.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.terminal = QComboBox()
        self.terminal.addItems(["Terminal", "iTerm2"])
        self.terminal.setCurrentText(settings.mac_terminal)
        form.addRow("macOS terminal", self.terminal)

    def save(self):
        self.settings.set_mac_terminal(self.terminal.currentText())
