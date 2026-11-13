"""A read-only view of what the kubeconfig says about one context, shown before the cluster is connected."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


class ContextEntryDialog(QDialog):
    def __init__(self, context, lines, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Kubeconfig entry: {context}")
        self.setObjectName("contextEntryDialog")
        self.resize(680, 300)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        text.setAccessibleName("Kubeconfig entry")
        text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
