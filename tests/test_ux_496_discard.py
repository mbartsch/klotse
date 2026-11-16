"""#496: a structured editor asks before its edits are thrown away on Esc, the X or Cancel; the YAML and form hand-offs never ask."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui.data_editor import DataEditDialog
from test_data_editor import CM_RAW


def shown_form(qtbot):
    dlg = DataEditDialog("ConfigMap", dict(CM_RAW))
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    return dlg


def answer(monkeypatch, value):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or value)
    return asked


def test_escape_after_an_edit_asks_and_no_keeps_the_form_open(qtbot, monkeypatch):
    dlg = shown_form(qtbot)
    QTest.keyClicks(dlg.data.new_key, "token")  # a user edit
    asked = answer(monkeypatch, QMessageBox.No)
    with qtbot.assertNotEmitted(dlg.rejected):
        dlg.reject()
    assert asked and "Discard" in asked[0]


def test_escape_after_an_edit_with_yes_closes(qtbot, monkeypatch):
    dlg = shown_form(qtbot)
    QTest.keyClicks(dlg.data.new_key, "token")
    answer(monkeypatch, QMessageBox.Yes)
    with qtbot.waitSignal(dlg.rejected):
        dlg.reject()


def test_closing_without_an_edit_does_not_ask(qtbot, monkeypatch):
    dlg = shown_form(qtbot)
    asked = answer(monkeypatch, QMessageBox.No)
    with qtbot.waitSignal(dlg.rejected):
        dlg.reject()
    assert asked == []


def test_the_yaml_hand_off_never_asks(qtbot, monkeypatch):
    dlg = shown_form(qtbot)
    QTest.keyClicks(dlg.data.new_key, "token")
    dlg.wants_yaml = True  # "Edit as YAML…" sets this and rejects the dialog
    asked = answer(monkeypatch, QMessageBox.No)
    with qtbot.waitSignal(dlg.rejected):
        dlg.reject()
    assert asked == []
