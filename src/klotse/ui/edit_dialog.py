import yaml
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QTextBrowser, QVBoxLayout)

from . import theme
from .manifest import dump_manifest
from .schema_help import SchemaHelper


def leave_for_yaml(form):
    """"Edit as YAML…" of a form dialog: remember what the form holds right now in `form.pending` (so the YAML editor shows
    the pending changes instead of the original), flag `wants_yaml` and close the form. A form that cannot produce a
    manifest (`pending_body()` gives None) asks first, because its changes would be left behind."""
    body = form.pending_body()
    if body is None and QMessageBox.question(
            form, "Edit as YAML", "The form's current values cannot be turned into YAML yet, so switching leaves them "
            "behind. Switch anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
        return
    form.pending = body
    form.wants_yaml = True
    form.reject()


class EditDialog(QDialog):
    """Edit a resource as YAML; `result_body` holds the parsed manifest after Save. With `can_form`, "Edit in form…" parses
    the text and leaves with `wants_form` set and the manifest as `pending_body`."""

    def __init__(self, title, raw, parent=None, note="", can_form=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 600)
        self.result_body = None
        self.wants_form, self.pending_body = False, None
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
        lay.addWidget(self.editor, 1)
        # schema help: field documentation and completion from the cluster's OpenAPI (loaded by the owner with set_schema)
        self.docs = QTextBrowser()
        self.docs.setMaximumHeight(130)
        self.docs.setOpenLinks(False)
        self.fields_status = QLabel("")
        self.fields_status.setStyleSheet(theme.warning_style())
        self.fields_status.setWordWrap(True)
        lay.addWidget(self.docs)
        lay.addWidget(self.fields_status)
        self.help = SchemaHelper(self.editor, self.docs, self.fields_status, self)
        self.form_button = QPushButton("Edit in form…")
        self.form_button.setToolTip("Go back to the form with the changes made here")
        self.form_button.clicked.connect(self._to_form)
        buttons.addButton(self.form_button, QDialogButtonBox.ActionRole)
        self.form_button.setVisible(can_form)  # after addButton, which shows it
        lay.addWidget(buttons)

    def _parse(self):
        """The manifest in the text, or None after telling the user what is wrong with it."""
        try:
            body = yaml.safe_load(self.editor.toPlainText())
            if not isinstance(body, dict) or not (body.get("metadata") or {}).get("name"):
                raise ValueError("Manifest must be a mapping with metadata.name")
        except (yaml.YAMLError, ValueError) as e:
            QMessageBox.warning(self, "Invalid YAML", str(e))
            return None
        return body

    def _to_form(self):
        body = self._parse()
        if body is not None:
            self.pending_body, self.wants_form = body, True
            self.reject()

    def _save(self):
        body = self._parse()
        if body is None:
            return
        self.result_body = body
        self.accept()
