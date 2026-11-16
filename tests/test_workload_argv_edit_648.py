"""#648 follow-up: once the command or args box is edited, every argv value still reads back exactly: empty values, spaces,
line breaks, carriage returns and leading quotes are kept, and an empty line is an empty value, not a dropped one."""
from klotse.ui.workload_widgets import LinesEdit, decode_lines, encode_lines

ARGV = ["--x", "", "  ", "a\nb", "c\rd", "tab\there", '"quoted"', "trailing ", "plain"]


def test_the_shown_text_reads_back_as_the_same_values():
    assert decode_lines(encode_lines(ARGV)) == ARGV


def test_an_edited_box_keeps_every_value(qtbot):
    edit = LinesEdit(["old"], "Arguments")
    qtbot.addWidget(edit)
    edit.setPlainText(encode_lines(ARGV))  # the user edits the box; the same text is read back
    assert edit.lines() == ARGV


def test_a_blank_line_typed_in_the_box_is_an_empty_value(qtbot):
    edit = LinesEdit([], "Arguments")
    qtbot.addWidget(edit)
    edit.setPlainText("first\n\nsecond\n")
    assert edit.lines() == ["first", "", "second"]


def test_the_line_break_after_the_last_value_is_not_a_value(qtbot):
    edit = LinesEdit([], "Arguments")
    qtbot.addWidget(edit)
    edit.setPlainText("one\n")
    assert edit.lines() == ["one"]
    edit.setPlainText("")
    assert edit.lines() == []


def test_a_json_string_in_the_box_is_read_as_one_value():
    assert decode_lines('"a\\nb"\nplain') == ["a\nb", "plain"]
    assert decode_lines('"not closed\nplain') == ['"not closed', "plain"]  # not valid JSON: kept as written
