import yaml
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout

from . import theme
from .manifest import dump_manifest


class EditDialog(QDialog):
    """Edit a resource as YAML; `result_body` holds the parsed manifest after Save."""

    def __init__(self, title, raw, parent=None, note=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 600)
        self.result_body = None
        self.editor = QPlainTextEdit(dump_manifest(raw))
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Saving replaces the live object (like kubectl replace). Many fields are immutable."))
        if note:  # something to know before changing this object (RBAC: aggregated or system roles)
            warning = QLabel(note)
            warning.setWordWrap(True)
            warning.setStyleSheet(theme.warning_style())
            lay.addWidget(warning)
        lay.addWidget(self.editor)
        lay.addWidget(buttons)

    def _save(self):
        try:
            body = yaml.safe_load(self.editor.toPlainText())
            if not isinstance(body, dict) or "name" not in (body.get("metadata") or {}):
                raise ValueError("Manifest must be a mapping with metadata.name")
        except (yaml.YAMLError, ValueError) as e:
            QMessageBox.warning(self, "Invalid YAML", str(e))
            return
        self.result_body = body
        self.accept()
