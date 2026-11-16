"""Questions before a destructive action name the action on the button (#500): "Delete", "Terminate", "Uninstall" instead of
Yes, with Cancel as the default. The arguments are those of QMessageBox.question plus the verb, so a call site changes by
one keyword. Callers compare the result with QMessageBox.Yes as before."""
from PySide6.QtWidgets import QMessageBox


def verb_question(parent, title, text, buttons=None, default=None, verb="Yes"):
    box = QMessageBox(QMessageBox.Warning, title, text, parent=parent)
    go = box.addButton(verb, QMessageBox.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return QMessageBox.Yes if box.clickedButton() is go else QMessageBox.No


ORIGINAL = verb_question  # the real one, for tests of the buttons (the test suite routes verb_question to QMessageBox.question)
