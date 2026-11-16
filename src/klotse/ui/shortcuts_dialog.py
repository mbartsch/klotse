"""#501: Help > Keyboard shortcuts. The list is read from the menus themselves, so it cannot drift from what the keys do."""
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout


def shortcut_rows(menu_bar):
    """(menu path, shortcut) for every menu entry that has a shortcut, in menu order. Mnemonic '&' marks are dropped."""
    rows = []

    def clean(text):
        return text.replace("&", "").rstrip("…").strip()

    def walk(menu, path):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                walk(action.menu(), path + [clean(action.text())])
            elif action.shortcut().toString() and action.text():
                rows.append((" > ".join(path + [clean(action.text())]),
                             action.shortcut().toString(QKeySequence.NativeText)))

    for action in menu_bar.actions():
        if action.menu() is not None:
            walk(action.menu(), [clean(action.text())])
    return rows


class ShortcutsDialog(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setObjectName("shortcutsDialog")
        layout = QVBoxLayout(self)
        if rows:
            layout.addWidget(QLabel("Each shortcut as the menu shows it."))
        else:
            layout.addWidget(QLabel("No menu entry has a keyboard shortcut."))
        self.table = QTableWidget(len(rows), 2)
        self.table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for r, (action, keys) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(action))
            self.table.setItem(r, 1, QTableWidgetItem(keys))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, 420)
        self.table.setAccessibleName("Keyboard shortcuts")
