"""Ask before a form's edits are thrown away (#496).

The structured editors (workload, network, role, binding, service account, data) close on Esc, the window's X and Cancel
without asking, so typed changes were lost. `DiscardGuard` is a mixin for those dialogs: once the dialog is first shown it
notices what the user changes (typing, choosing, toggling, adding or removing rows), and a plain close then asks, with No as
the default. "Edit as YAML…" and "Edit in form…" leave on purpose and never ask.

Only user changes count: text set by the program while the form is filled is not an edit.
"""
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QComboBox, QDialogButtonBox, QLineEdit, QMessageBox, QPlainTextEdit, \
    QPushButton, QRadioButton, QSpinBox, QTableWidget, QTreeWidget, QListWidget


def _mark(dialog):
    def mark(*_args):
        dialog._edited = True
    return mark


def _children(dialog, *kinds):
    """Every child of the given Qt types (PySide6 takes one type per search)."""
    return [child for kind in kinds for child in dialog.findChildren(kind)]


def watch_edits(dialog):
    """Connect the user-driven signals of the dialog's controls to the flag. Called on the first show, after the form is
    filled, so the program's own setup is not counted. Save, Cancel and the button box's buttons are not edits; Add,
    Remove and Edit… buttons are."""
    mark = _mark(dialog)
    box_buttons = {b for box in dialog.findChildren(QDialogButtonBox) for b in box.buttons()}
    for line in dialog.findChildren(QLineEdit):
        line.textEdited.connect(mark)
    for text in dialog.findChildren(QPlainTextEdit):
        text.textChanged.connect(mark)
    for combo in dialog.findChildren(QComboBox):
        combo.activated.connect(mark)
    for box in _children(dialog, QCheckBox, QRadioButton):
        box.toggled.connect(mark)
    for spin in dialog.findChildren(QSpinBox):
        spin.valueChanged.connect(mark)
    for button in dialog.findChildren(QPushButton):
        if button not in box_buttons:
            button.clicked.connect(mark)
    for table in _children(dialog, QTableWidget, QTreeWidget, QListWidget):
        table.itemChanged.connect(mark)


def confirm_discard(dialog):
    """True when the dialog may close: nothing was edited, or the user agrees to discard, or it leaves for YAML or the form."""
    if getattr(dialog, "wants_yaml", False) or getattr(dialog, "wants_form", False):
        return True
    if not getattr(dialog, "_edited", False):
        return True
    return QMessageBox.question(dialog, "Discard changes", "Discard the changes to this object?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


class DiscardGuard:
    """Put this before QDialog in a dialog's bases. Esc, the window's X and Cancel go through reject()."""

    _guard_watching = False

    def showEvent(self, event):  # noqa: N802 - Qt name
        if not self._guard_watching:
            self._guard_watching = True
            watch_edits(self)
        super().showEvent(event)

    def reject(self):
        if confirm_discard(self):
            super().reject()
