import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui.terminal_settings import TerminalSettings, TerminalSettingsDialog


def test_terminal_preference_default_persistence_and_invalid_value(tmp_path):
    path = str(tmp_path / "settings.ini")
    settings = TerminalSettings(QSettings(path, QSettings.IniFormat))
    assert settings.mac_terminal == "Terminal"
    settings.set_mac_terminal("iTerm2")
    loaded = TerminalSettings(QSettings(path, QSettings.IniFormat))
    assert loaded.mac_terminal == "iTerm2"
    with pytest.raises(ValueError):
        settings.set_mac_terminal("other")
    loaded.store.setValue("terminal/macos", "other")
    assert TerminalSettings(loaded.store).mac_terminal == "Terminal"


def test_terminal_dialog_save_cancel_and_save_failure(qtbot, tmp_path, monkeypatch):
    settings = TerminalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    dialog = TerminalSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.terminal.setCurrentText("iTerm2")
    dialog.reject()
    assert settings.mac_terminal == "Terminal"
    dialog._save()
    assert settings.mac_terminal == "iTerm2"
    assert dialog.result() == QDialog.Accepted

    broken = SimpleNamespace(value=lambda *args: "Terminal", setValue=lambda *args: None,
                             sync=lambda: None, status=lambda: QSettings.AccessError,
                             fileName=lambda: "read-only.ini")
    settings = TerminalSettings(broken)
    dialog = TerminalSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.terminal.setCurrentText("iTerm2")
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog._save()
    assert errors and "read-only.ini" in errors[0]
    assert settings.mac_terminal == "Terminal"
    assert dialog.result() != QDialog.Accepted


@pytest.mark.parametrize("app", ["Terminal", "iTerm2"])
def test_macos_terminal_quoting_and_failure(monkeypatch, app):
    from klotse.ui import terminal
    monkeypatch.setattr(terminal.sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(terminal.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    argv = ["kubectl", "--context", "cluster;$(echo nope)", "exec", "pod", "--", "sh"]
    terminal.open_terminal(argv, app)
    command = calls[0][0][0]
    assert command[:2] == ["osascript", "-e"]
    assert f'tell application "{app}"' in command[2]
    assert argv[2] not in command[2]
    wrapper = shlex.split(command[3])
    assert wrapper[:2] == ["exec", sys.executable]
    assert wrapper[2].endswith("terminal_session.py")
    assert wrapper[3] == app
    assert json.loads(wrapper[4]) == argv
    assert calls[0][1]["timeout"] == 15

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="Automation denied")
    monkeypatch.setattr(terminal.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match=f"Could not open {app}: Automation denied"):
        terminal.open_terminal(argv, app)


@pytest.mark.parametrize("status", [0, 7])
def test_mac_session_success_closes_and_failure_waits_for_acknowledgement(monkeypatch, capsys, status):
    from klotse.ui import terminal_session
    calls = []
    monkeypatch.setattr(terminal_session.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(terminal_session.subprocess, "call", lambda argv: status)
    def acknowledge():
        assert not calls  # Keep the failed session open until the user presses Enter.
        return ""
    monkeypatch.setattr("builtins.input", acknowledge)
    assert terminal_session.run_session(["kubectl", "attach", "pod"], "Terminal",
                                        ["window-id", "/dev/tty-test"]) == status
    command = calls[0][0][0]
    assert command[-2:] == ["window-id", "/dev/tty-test"]
    assert command[2] == terminal_session.TERMINAL_CLOSE_SCRIPT
    assert calls[0][1]["start_new_session"] is True
    if status == 0:
        assert "Press Enter" not in capsys.readouterr().out
    else:
        assert "status 7. Press Enter to close" in capsys.readouterr().out


def test_preference_shared_with_existing_and_future_workspaces(qtbot, tmp_path, monkeypatch):
    from test_app import FakeService
    from klotse.ui import main_window, workspace
    from klotse.ui.customizations import LabelSettings

    monkeypatch.setattr(main_window.sys, "platform", "darwin")
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window = main_window.MainWindow(FakeService, label_settings=LabelSettings(store))
    qtbot.addWidget(window)
    first = window.workspace("ctx")
    qtbot.waitUntil(lambda: first._connected and not first._loading)
    assert window.terminal_settings_action.text() == "Terminal…"
    calls = []
    monkeypatch.setattr(workspace, "open_terminal", lambda *args: calls.append(args))
    monkeypatch.setattr(workspace.ClusterWorkspace, "_run",
                        lambda self, fn, cb, *args: cb(fn(*args)))
    argv = ["kubectl", "--context", "ctx"]
    first._launch_terminal(argv, "opened")
    assert calls[-1] == (argv, "Terminal")
    def choose_iterm(dialog):
        dialog.terminal.setCurrentText("iTerm2")
        dialog._save()
        return dialog.result()
    monkeypatch.setattr(TerminalSettingsDialog, "exec", choose_iterm)
    window.terminal_settings_action.trigger()
    first._launch_terminal(argv, "opened")
    assert calls[-1] == (argv, "iTerm2")
    # Avoid starting connection workers; only exercise workspace creation/sharing.
    monkeypatch.setattr(workspace.ClusterWorkspace, "start", lambda self: None)
    window.open_cluster("other")
    second = window.workspace("other")
    second._launch_terminal(argv, "opened")
    assert calls[-1] == (argv, "iTerm2")
    assert first.terminal_settings is second.terminal_settings is window.terminal_settings
    window.shutdown()
