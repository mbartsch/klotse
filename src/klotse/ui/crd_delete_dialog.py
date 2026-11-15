"""The second question before a CustomResourceDefinition is deleted: every object of that type, in a scrollable list, so the
user sees what goes with it (#503, owner's decision: warn, count, and ask twice)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .dialog_sizing import resize_dialog


class CrdDeleteDialog(QDialog):
    """`objects` are the custom objects that Deleting the definition removes: (namespace, name, created) tuples. Delete is the
    default only after a deliberate click on it; Cancel is the default button."""

    def __init__(self, kind, crd_name, objects, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Delete {crd_name}")
        resize_dialog(self, 720, 480)
        count = len(objects)
        self.summary = QLabel(
            f"Deleting the definition <b>{crd_name}</b> deletes all <b>{count}</b> {kind} object{'s' if count != 1 else ''} "
            "in the cluster, in every namespace. Kubernetes does not keep them. This cannot be undone.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        self.table = QTableWidget(count, 3)
        self.table.setHorizontalHeaderLabels(["Namespace", "Name", "Created"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for row, (namespace, name, created) in enumerate(objects):
            for column, text in enumerate((namespace or "(cluster-scoped)", name, created or "")):
                self.table.setItem(row, column, QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.delete_button = self.buttons.addButton(f"Delete the definition and {count} object{'s' if count != 1 else ''}",
                                                    QDialogButtonBox.DestructiveRole)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setDefault(False)
        self.buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        self.buttons.rejected.connect(self.reject)
        self.delete_button.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.table, 1)  # the list takes the room and scrolls
        lay.addWidget(self.buttons)
