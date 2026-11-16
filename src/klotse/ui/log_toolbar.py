"""The controls both log tabs share (#500): the regular-expression filters with Ignore case, Find (Enter = next), Wrap,
Save, Restart and Clear, and the same tail rule (0 = all lines). Each tab keeps its pod controls (the container, the previous
instance of a single pod; the label selector, the container filter and the time range of several pods)."""
import re

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from . import theme

MAX_LINE_MATCH = 4000  # a regular expression looks at no more than this many characters of a line


def compile_filters(match_box, drop_box, ignore_case, status):
    """(show-only expression, hide expression) compiled from the two boxes; a bad one filters nothing and says so (#498)."""
    flags = re.IGNORECASE if ignore_case.isChecked() else 0
    compiled = []
    for box in (match_box, drop_box):
        text = box.text()
        if not text:
            compiled.append(None)
            continue
        try:
            compiled.append(re.compile(text, flags))
            box.setStyleSheet("")
        except re.error as error:
            box.setStyleSheet(theme.warning_style())
            box.setToolTip(f"Not a valid regular expression: {error}")
            status.setText(f"Filter not valid: {error}")  # the words, not only the colour (#498)
            compiled.append(None)
    return compiled[0], compiled[1]


def line_shown(line, show, hide):
    text = line[:MAX_LINE_MATCH]
    if show is not None and not show.search(text):
        return False
    return not (hide is not None and hide.search(text))


def find_next(text_edit, term, no_match):
    """Move to the next match of `term`, wrapping to the start; `no_match` is shown when nothing matches (#497)."""
    if not term:
        return
    if not text_edit.find(term):
        text_edit.moveCursor(QTextCursor.Start)
        no_match.setVisible(not text_edit.find(term))
    else:
        no_match.hide()


def save_visible(parent, text, status):
    """Write the lines on screen to a file the user picks; returns the path or None."""
    path, _ = QFileDialog.getSaveFileName(parent, "Save logs", "logs.txt", "Text files (*.txt);;All files (*)")
    if not path:
        return None
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as error:
        QMessageBox.warning(parent, "Save logs", str(error))
        return None
    status.setText(f"Saved to {path}")
    return path
