from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QWidget)


class KubeconfigSettings(QObject):
    """The kubeconfig Kubeman reads contexts from: a custom file, or None for KUBECONFIG / ~/.kube/config.

    Saved as `kubeconfig/path` in the shared settings store. A saved file that no longer exists is ignored.
    """

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else QSettings(
            QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman")
        saved = str(self.store.value("kubeconfig/path", "") or "")
        self.path = saved if saved and Path(saved).is_file() else None
        self.missing = saved if saved and self.path is None else None  # remembered so the UI can say why it is ignored

    def set_path(self, path):
        path = str(path or "").strip() or None
        if path and not Path(path).is_file():
            raise ValueError(f"Kubeconfig file not found: {path}")
        if path:
            self.store.setValue("kubeconfig/path", path)
        else:
            self.store.remove("kubeconfig/path")
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")
        self.path, self.missing = path, None


def open_folder(folder):
    """Show a folder in Explorer / Finder / the file manager; returns an error message or None."""
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)  # nothing is saved until the first customization
    except OSError as error:
        return f"Cannot create {folder}: {error}"
    return None if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))) else f"Could not open {folder}"


class PreferencesDialog(QDialog):
    """Application preferences. Accepting only validates; the caller saves/applies `chosen_kubeconfig()`."""

    def __init__(self, kubeconfig, config_folder, parent=None):
        super().__init__(parent)
        self.kubeconfig, self.config_folder = kubeconfig, Path(config_folder)
        self.setWindowTitle("Preferences")
        self.resize(640, 0)
        form = QFormLayout(self)

        self.path = QLineEdit(kubeconfig.path or "", readOnly=True)
        self.path.setPlaceholderText("Default (KUBECONFIG or ~/.kube/config)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        default = QPushButton("Use default")
        default.clicked.connect(lambda: self.path.clear())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        row.addWidget(default)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Kubeconfig", holder)
        self.note = QLabel("Changing the kubeconfig closes the open clusters and reloads the context list.\n"
                           "kubectl commands (shell, attach, drain) use the same file.")
        self.note.setWordWrap(True)
        if kubeconfig.missing:
            self.note.setText(f"Saved kubeconfig not found, using the default: {kubeconfig.missing}\n" + self.note.text())
        form.addRow(self.note)

        self.open_button = QPushButton("Open config folder")
        self.open_button.setToolTip("Show Kubeman's settings file in Explorer / Finder / the file manager (debug)")
        self.open_button.clicked.connect(self._open_folder)
        form.addRow("Settings", self.open_button)
        form.addRow("", QLabel(str(self.config_folder)))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        start = self.path.text() or str(Path.home() / ".kube")
        chosen, _ = QFileDialog.getOpenFileName(self, "Select kubeconfig", start, "All files (*)")
        if chosen:
            self.path.setText(chosen)

    def _open_folder(self):
        error = open_folder(self.config_folder)
        if error:
            QMessageBox.warning(self, "Cannot open config folder", error)

    def chosen_kubeconfig(self):
        """The kubeconfig path picked in the dialog (None = default)."""
        return self.path.text().strip() or None

    def _save(self):
        chosen = self.chosen_kubeconfig()
        if chosen and not Path(chosen).is_file():
            QMessageBox.warning(self, "Cannot save preferences", f"Kubeconfig file not found: {chosen}")
            return
        self.accept()  # the caller saves and applies it (closing open clusters needs confirmation first)
