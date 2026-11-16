"""#648 (C1): saving a workload form without touching the command or args keeps every argv value exactly, including empty
strings, whitespace-only values, and values with a newline or carriage return. Editing the text still works as before."""
from PySide6.QtWidgets import QPlainTextEdit

from klotse.ui.workload_widgets import LinesEdit

ARGV = ["--x", "", "  ", "a\nb", "c\rd", "tab\there"]


def test_an_unedited_argv_comes_back_exactly(qtbot):
    edit = LinesEdit(ARGV, "Arguments")
    qtbot.addWidget(edit)
    assert edit.lines() == ARGV


def test_an_unedited_argv_is_not_changed_by_reading_it_twice(qtbot):
    edit = LinesEdit(ARGV, "Arguments")
    qtbot.addWidget(edit)
    assert edit.lines() == edit.lines() == ARGV


def test_an_edited_argv_is_read_from_its_lines(qtbot):
    edit = LinesEdit(["old"], "Arguments")
    qtbot.addWidget(edit)
    edit.setPlainText("first\n\n  second  \n")
    assert edit.lines() == ["first", "  second  "]
    assert isinstance(edit, QPlainTextEdit)
