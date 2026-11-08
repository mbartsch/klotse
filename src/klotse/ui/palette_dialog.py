"""The command palette window (Ctrl+K): one search box over views, namespaces, clusters and actions; Enter runs the top choice.
The list of entries comes from the caller (`MainWindow.palette_entries`), the matching is `palette.rank`."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import palette, theme


class PaletteDialog(QDialog):
    def __init__(self, entries, parent=None, free_text=None):
        """`free_text(text)` may return one extra Entry for what was typed (searching objects for it, say)."""
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(640, 420)
        self.entries, self.free_text, self.shown = list(entries), free_text, []
        self.box = QLineEdit(placeholderText="Type to jump to a view, namespace, cluster or action…", clearButtonEnabled=True)
        self.list = QListWidget()
        self.hint = QLabel("↑ ↓ choose · Enter run · Esc close")
        theme.muted(self.hint)
        lay = QVBoxLayout(self)
        lay.addWidget(self.box)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.hint)
        self.box.textChanged.connect(self.refresh)
        self.box.installEventFilter(self)
        self.list.itemActivated.connect(lambda _item: self.run_current())
        self.list.itemClicked.connect(lambda _item: self.run_current())
        self.refresh("")

    def refresh(self, text=""):
        found = palette.rank(self.entries, text)
        extra = self.free_text(text.strip()) if (self.free_text and text.strip()) else None
        self.shown = found + ([extra] if extra is not None else [])
        self.list.clear()
        for entry in self.shown:
            item = QListWidgetItem(f"{entry.category}  ·  {entry.label}" + (f"   —   {entry.detail}" if entry.detail else ""))
            self.list.addItem(item)
        if self.shown:
            self.list.setCurrentRow(0)

    def eventFilter(self, watched, event):
        """Up and Down move through the list while the cursor stays in the box; Enter runs the choice."""
        if watched is self.box and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up) and self.shown:
                row = self.list.currentRow() + (1 if key == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(len(self.shown) - 1, row)))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
                return True
        return super().eventFilter(watched, event)

    def run_current(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.shown):
            return None
        entry = self.shown[row]
        self.accept()  # close first: the action may open another window
        if entry.run is not None:
            entry.run()
        return entry
