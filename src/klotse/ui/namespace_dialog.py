import re

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox,
                               QPlainTextEdit)

# RFC 1123 label, as required for namespace names
NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def parse_pairs(text):
    """'k=v' per line (blank lines ignored) -> dict. Raises ValueError on a malformed line."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"Expected key=value, got: {line!r}")
        out[key.strip()] = value.strip()
    return out


def build_namespace(name, labels, annotations, finalizers):
    meta = {"name": name}
    if labels:
        meta["labels"] = labels
    if annotations:
        meta["annotations"] = annotations
    if finalizers:
        meta["finalizers"] = finalizers
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": meta}


class NamespaceDialog(QDialog):
    """Form for a new namespace; `result_body` holds the manifest after Create."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New namespace")
        self.resize(480, 420)
        self.result_body = None
        self.name = QLineEdit(placeholderText="my-namespace")
        self.labels = QPlainTextEdit(placeholderText="one key=value per line, e.g.\nteam=platform")
        self.annotations = QPlainTextEdit(placeholderText="one key=value per line")
        self.finalizers = QPlainTextEdit(placeholderText="one per line (optional)")
        self.finalizers.setToolTip("Custom finalizers block deletion of the namespace until a controller removes them.")
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Labels", self.labels)
        form.addRow("Annotations", self.annotations)
        form.addRow("Finalizers", self.finalizers)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _create(self):
        name = self.name.text().strip()
        try:
            if not NAME_RE.match(name) or len(name) > 63:
                raise ValueError("Name must be lowercase alphanumeric or '-', start/end alphanumeric, max 63 chars")
            labels = parse_pairs(self.labels.toPlainText())
            annotations = parse_pairs(self.annotations.toPlainText())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid input", str(e))
            return
        finalizers = [f.strip() for f in self.finalizers.toPlainText().splitlines() if f.strip()]
        self.result_body = build_namespace(name, labels, annotations, finalizers)
        self.accept()
