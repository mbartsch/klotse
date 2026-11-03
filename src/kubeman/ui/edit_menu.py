from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QApplication, QLineEdit, QMenu, QPlainTextEdit, QTextEdit

TEXT_WIDGETS = (QLineEdit, QPlainTextEdit, QTextEdit)
COMMANDS = ("undo", "redo", "cut", "copy", "paste", "select_all")


def selected_table_text(view):
    """Selected rows of a table as tab-separated displayed text, one row per line, hidden columns left out."""
    model, selection = view.model(), view.selectionModel()
    rows = sorted({index.row() for index in selection.selectedIndexes()})
    columns = [c for c in range(model.columnCount()) if not view.isColumnHidden(c)]
    return "\n".join("\t".join(str(model.index(r, c).data() or "") for c in columns) for r in rows)


def capabilities(widget):
    """Which Edit commands make sense for the focused widget right now."""
    caps = dict.fromkeys(COMMANDS, False)
    if isinstance(widget, QLineEdit):
        writable = not widget.isReadOnly()
        caps.update(undo=writable and widget.isUndoAvailable(), redo=writable and widget.isRedoAvailable(),
                    cut=writable and widget.hasSelectedText(), copy=widget.hasSelectedText(),
                    paste=writable, select_all=bool(widget.text()))
    elif isinstance(widget, (QPlainTextEdit, QTextEdit)):
        writable = not widget.isReadOnly()
        document = widget.document()
        selected = widget.textCursor().hasSelection()
        caps.update(undo=writable and document.isUndoAvailable(), redo=writable and document.isRedoAvailable(),
                    cut=writable and selected, copy=selected, paste=writable,
                    select_all=not document.isEmpty())
    elif isinstance(widget, QAbstractItemView):
        caps.update(copy=widget.selectionModel() is not None and widget.selectionModel().hasSelection(),
                    select_all=widget.model() is not None and widget.model().rowCount() > 0)
    if caps["paste"]:
        caps["paste"] = bool(QGuiApplication.clipboard().text())
    return caps


def run(command, widget):
    """Perform an Edit command on a widget; unsupported combinations do nothing."""
    if widget is None or not capabilities(widget).get(command):
        return
    if isinstance(widget, QAbstractItemView):
        if command == "copy":
            QGuiApplication.clipboard().setText(selected_table_text(widget))
        elif command == "select_all":
            widget.selectAll()
        return
    getattr(widget, {"select_all": "selectAll"}.get(command, command))()


class EditActions(QObject):
    """Undo / Redo / Cut / Copy / Paste / Select All for whichever text box or table has focus.

    Opening a menu moves focus away from the widget, so the last focused editable widget is remembered. The
    actions stay enabled while the menu is closed so their keyboard shortcuts keep working; widgets that handle
    the same keys themselves (text boxes) still get them first.
    """

    def __init__(self, menu: QMenu, parent=None):
        super().__init__(parent or menu)
        self.menu = menu
        self.actions = {}
        self._last = None
        for command, text, shortcut in (
                ("undo", "Undo", QKeySequence.Undo), ("redo", "Redo", QKeySequence.Redo), (None, None, None),
                ("cut", "Cut", QKeySequence.Cut), ("copy", "Copy", QKeySequence.Copy),
                ("paste", "Paste", QKeySequence.Paste), (None, None, None),
                ("select_all", "Select All", QKeySequence.SelectAll)):
            if command is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda _=False, c=command: run(c, self.target()))
            menu.addAction(action)
            self.actions[command] = action
        QApplication.instance().focusChanged.connect(self._focus_changed)
        menu.aboutToShow.connect(self.refresh)
        menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self._enable_all))

    def _focus_changed(self, _old, new):
        if new is not None and not isinstance(new, QMenu) and isinstance(new, (*TEXT_WIDGETS, QAbstractItemView)):
            self._last = new

    def target(self):
        focused = QApplication.focusWidget()
        if isinstance(focused, (*TEXT_WIDGETS, QAbstractItemView)):
            return focused
        return self._last  # a menu or toolbar button has focus: act on the widget used last

    def refresh(self):
        caps = capabilities(self.target())
        for command, action in self.actions.items():
            action.setEnabled(caps[command])

    def _enable_all(self):
        for action in self.actions.values():
            action.setEnabled(True)
