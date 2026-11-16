

def test_the_key_value_table_is_at_least_its_height_and_grows(qtbot):
    from klotse.ui.service_account_editor import KeyValueEditor
    editor = KeyValueEditor("Labels", height=90)
    qtbot.addWidget(editor)
    assert editor.table.minimumHeight() == 90 and editor.table.maximumHeight() > 90


def test_the_role_pick_lists_are_at_least_their_height_and_grow(qtbot):
    from klotse.ui.role_editor import Picker
    picker = Picker(["a"], [])
    qtbot.addWidget(picker)
    assert picker.results.minimumHeight() == 96 and picker.results.maximumHeight() > 96
    assert picker.chosen.minimumHeight() == 70 and picker.chosen.maximumHeight() > 70
