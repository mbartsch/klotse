"""#496: closing a running terminal window asks first; a blank or repeated key name says why it was refused."""
import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.ui import embedded_terminal as et
from klotse.ui.data_editor import DataEditDialog
from test_embedded_terminal import fake_factory, needs_web
from test_data_editor import CM_RAW


def answer(monkeypatch, value):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or value)
    return asked


def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    return shown


@needs_web
def test_closing_a_running_terminal_asks_and_no_keeps_it(qtbot, monkeypatch):
    factory = fake_factory()
    tab = et.TerminalTab("pod/web", ["kubectl", "exec"], session_factory=factory)  # not qtbot-owned: the window deletes it on close
    tab.start()
    window = et.TerminalHost("pod/web", tab, None).window
    window.show()
    asked = answer(monkeypatch, QMessageBox.No)
    window.close()
    assert asked and not tab.ended and factory.made[0].stopped == 0
    answer(monkeypatch, QMessageBox.Yes)  # leave nothing running for teardown
    window.close()


@needs_web
def test_closing_a_running_terminal_after_yes_ends_it(qtbot, monkeypatch):
    factory = fake_factory()
    tab = et.TerminalTab("pod/web", ["kubectl", "exec"], session_factory=factory)  # not qtbot-owned: the window deletes it on close
    tab.start()
    window = et.TerminalHost("pod/web", tab, None).window
    window.show()
    answer(monkeypatch, QMessageBox.Yes)
    window.close()
    assert tab.ended and factory.made[0].stopped == 1


def test_a_blank_new_key_says_so(qtbot, monkeypatch):
    dlg = DataEditDialog("ConfigMap", dict(CM_RAW))
    qtbot.addWidget(dlg)
    shown = warnings(monkeypatch)
    dlg.data.new_key.setText("   ")
    dlg.data._add()
    assert shown and "Type a name" in shown[0]


def test_a_repeated_new_key_says_it_already_exists(qtbot, monkeypatch):
    dlg = DataEditDialog("ConfigMap", dict(CM_RAW))
    qtbot.addWidget(dlg)
    existing = dlg.data.keys_in_use()[0]
    shown = warnings(monkeypatch)
    dlg.data.new_key.setText(existing)
    dlg.data._add()
    assert shown and f"“{existing}” already exists" in shown[0]
    assert dlg.data.keys_in_use().count(existing) == 1
