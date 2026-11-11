import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.terminal_settings import TerminalPanel, TerminalSettings


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


def test_terminal_page_saves_only_on_save_and_reports_a_save_failure(qtbot, tmp_path, monkeypatch):
    settings = TerminalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    panel = TerminalPanel(settings)
    qtbot.addWidget(panel)
    panel.terminal.setCurrentText("iTerm2")
    assert settings.mac_terminal == "Terminal"  # choosing is not saving
    panel.save()
    assert settings.mac_terminal == "iTerm2"

    broken = SimpleNamespace(value=lambda *args: "Terminal", setValue=lambda *args: None,
                             sync=lambda: None, status=lambda: QSettings.AccessError,
                             fileName=lambda: "read-only.ini")
    settings = TerminalSettings(broken)
    panel = TerminalPanel(settings)
    qtbot.addWidget(panel)
    panel.terminal.setCurrentText("iTerm2")
    with pytest.raises(OSError, match="read-only.ini"):
        panel.save()
    assert settings.mac_terminal == "Terminal"


def test_preferences_saves_the_terminal_and_a_failure_keeps_the_dialog_open(qtbot, tmp_path, monkeypatch):
    kubeconfig = KubeconfigSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat))
    settings = TerminalSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    dialog = PreferencesDialog(kubeconfig, tmp_path / "cfg", terminal=settings)
    qtbot.addWidget(dialog)
    dialog.terminal_panel.terminal.setCurrentText("iTerm2")
    dialog.reject()
    assert settings.mac_terminal == "Terminal"  # Cancel keeps the old choice
    dialog._save()
    assert settings.mac_terminal == "iTerm2" and dialog.result() == QDialog.Accepted

    broken = SimpleNamespace(value=lambda *args: "Terminal", setValue=lambda *args: None,
                             sync=lambda: None, status=lambda: QSettings.AccessError,
                             fileName=lambda: "read-only.ini")
    dialog = PreferencesDialog(kubeconfig, tmp_path / "cfg", terminal=TerminalSettings(broken))
    qtbot.addWidget(dialog)
    dialog.terminal_panel.terminal.setCurrentText("iTerm2")
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog._save()
    assert errors and "read-only.ini" in errors[0] and dialog.result() != QDialog.Accepted


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
    assert wrapper[:2] == ["exec", "/bin/sh"]
    script = Path(wrapper[2]).read_text()
    assert sys.executable in script and app in script and json.dumps(argv) in script
    Path(wrapper[2]).unlink()
    assert calls[0][1]["timeout"] == 15

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="Automation denied")
    monkeypatch.setattr(terminal.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match=f"Could not open {app}: Automation denied"):
        terminal.open_terminal(argv, app)


def test_linux_terminal_uses_a_unique_script_instead_of_inline_command(monkeypatch, tmp_path):
    from klotse.ui import terminal
    monkeypatch.setattr(terminal.sys, "platform", "linux")
    real_mkstemp = terminal.tempfile.mkstemp
    monkeypatch.setattr(terminal.tempfile, "mkstemp", lambda **kwargs: real_mkstemp(dir=tmp_path, **kwargs))
    monkeypatch.setattr(terminal.shutil, "which", lambda name: "/usr/bin/x-terminal-emulator" if name == "x-terminal-emulator" else None)
    calls = []
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda argv: calls.append(argv))
    argv = ["kubectl", "--context", "cluster;$(echo nope)", "exec", "pod"]
    terminal.open_terminal(argv)
    assert calls[0][:2] == ["/usr/bin/x-terminal-emulator", "-e"]
    assert calls[0][2] == "/bin/sh"  # mkstemp scripts are readable but not executable
    assert Path(calls[0][-1]).parent == tmp_path
    script = Path(calls[0][-1]).read_text()
    assert "cluster;$(echo nope)" in script
    assert "cluster;$(echo nope)" not in " ".join(calls[0])
    assert "Session ended. Press Enter to close." in script
    assert "trap 'rm -f -- \"$0\"' EXIT" in script
    Path(calls[0][-1]).unlink()


@pytest.mark.parametrize("frozen,pythonw", [(False, False), (False, True), (True, False)])
def test_windows_terminal_passes_argv_as_data_to_its_own_console(monkeypatch, tmp_path, frozen, pythonw):
    from klotse.ui import terminal
    monkeypatch.setattr(terminal.sys, "platform", "win32")
    monkeypatch.setattr(terminal.sys, "frozen", frozen, raising=False)
    if pythonw:
        monkeypatch.setattr(terminal.sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setattr(terminal.subprocess, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    real_mkstemp = terminal.tempfile.mkstemp
    monkeypatch.setattr(terminal.tempfile, "mkstemp", lambda **kwargs: real_mkstemp(dir=tmp_path, **kwargs))
    calls = []
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    argv = ["kubectl.exe", "--context", "cluster&nope%PATH%!", "exec", "pod"]
    terminal.open_terminal(argv)
    runner, options = calls[0]
    assert runner[0] == (str(tmp_path / "python.exe") if pythonw else sys.executable)
    if frozen:
        assert runner[1] == "--terminal-session"
    else:
        assert runner[1].endswith("terminal_session.py")
    assert runner[-2] == "Windows"
    assert json.loads(Path(runner[-1]).read_text()) == argv
    assert argv[2] not in " ".join(runner)
    assert options["creationflags"] == subprocess.CREATE_NEW_CONSOLE
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    Path(runner[-1]).unlink()


@pytest.mark.parametrize("status", [0, 7])
def test_windows_session_preserves_real_process_arguments_and_exit_status(tmp_path, status):
    report = tmp_path / "received.json"
    command_file = tmp_path / "command.json"
    args = ["prod&ver", "%PATH%", "!EXPAND!", "a|b>c<d^e", 'quoted "value"', "with spaces",
            "caf\u00e9 \u96ea", "", "trailing\\", json.dumps({"spec": {"nodeName": 'node-"quoted"'}})]
    code = "import json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding='utf-8');sys.exit(" + str(status) + ")"
    command_file.write_text(json.dumps([sys.executable, "-c", code, str(report), *args]), encoding="utf-8")
    runner = "import sys;from klotse.ui.terminal_session import run_windows_session;sys.exit(run_windows_session(sys.argv[1]))"
    result = subprocess.run([sys.executable, "-c", runner, str(command_file)], input="\n", capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == status, result.stderr
    assert json.loads(report.read_text(encoding="utf-8")) == args
    assert "Press Enter to close" in result.stdout
    assert not command_file.exists()


def test_windows_runner_dispatch_opens_console_before_reading_command(monkeypatch):
    from klotse import cli
    from klotse.ui import terminal_session

    calls = []
    monkeypatch.setattr(terminal_session, "_windows_console", lambda: calls.append("console"))
    monkeypatch.setattr(terminal_session, "run_windows_session", lambda path: calls.append(path) or 7)
    assert cli.handle(["--terminal-session", "Windows", "command.json"]) == 7
    assert calls == ["console", "command.json"]


def test_windows_session_missing_command_is_reported_and_waits_for_enter(tmp_path, monkeypatch, capsys):
    from klotse.ui import terminal_session

    command = tmp_path / "command.json"
    command.write_text(json.dumps(["missing-kubectl.exe"]))
    def missing(*args, **kwargs):
        raise FileNotFoundError("kubectl was not found")
    monkeypatch.setattr(terminal_session.subprocess, "call", missing)
    acknowledgements = []
    monkeypatch.setattr("builtins.input", lambda: acknowledgements.append(True) or "")
    assert terminal_session.run_windows_session(command) == 127
    assert "kubectl was not found" in capsys.readouterr().out
    assert acknowledgements == [True] and not command.exists()


def test_windows_console_reopens_all_standard_streams(monkeypatch):
    import ctypes
    import io
    from klotse.ui import terminal_session

    calls = []
    streams = [io.StringIO() for _ in range(3)]
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        AllocConsole=lambda: calls.append("allocate"))), raising=False)
    def opened(path, mode, **kwargs):
        calls.append((path, mode, kwargs))
        return streams[len(calls) - 2]
    monkeypatch.setattr("builtins.open", opened)
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(terminal_session.sys, name, None)
    terminal_session._windows_console()
    assert calls == ["allocate", ("CONIN$", "r", {"encoding": "utf-8"}),
                     ("CONOUT$", "w", {"encoding": "utf-8", "buffering": 1}),
                     ("CONOUT$", "w", {"encoding": "utf-8", "buffering": 1})]
    assert [sys.stdin, sys.stdout, sys.stderr] == streams


@pytest.mark.skipif(os.name == "nt", reason="Runs a real POSIX shell; Windows covers the launcher argv above")
def test_posix_session_script_runs_without_execute_permission_and_removes_itself(tmp_path, monkeypatch):
    from klotse.ui import terminal

    report = tmp_path / "received.json"
    args = ["context;$(echo nope)", "quoted 'value'", "with spaces"]
    real_mkstemp = terminal.tempfile.mkstemp
    monkeypatch.setattr(terminal.tempfile, "mkstemp", lambda **kw: real_mkstemp(dir=tmp_path, **kw))
    code = "import json,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]));sys.exit(7)"
    path = terminal._external_session_script([sys.executable, "-c", code, str(report), *args])
    assert not os.access(path, os.X_OK)
    result = subprocess.run(["/bin/sh", path], input="\n", text=True, capture_output=True, timeout=20)
    assert result.returncode == 7
    assert json.loads(report.read_text()) == args
    assert not Path(path).exists()


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
    from test_app import FakeService, open_active
    from klotse.ui import main_window, workspace
    from klotse.ui.customizations import LabelSettings

    monkeypatch.setattr(main_window.sys, "platform", "darwin")
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    window = main_window.MainWindow(FakeService, label_settings=LabelSettings(store))
    qtbot.addWidget(window)
    open_active(window)
    first = window.workspace("ctx")
    qtbot.waitUntil(lambda: first._connected and not first._loading)
    calls = []
    monkeypatch.setattr(workspace, "open_terminal", lambda *args: calls.append(args))
    monkeypatch.setattr(workspace.ClusterWorkspace, "_run",
                        lambda self, fn, cb, *args: cb(fn(*args)))
    argv = ["kubectl", "--context", "ctx"]
    first._launch_terminal(argv, "opened")
    assert calls[-1] == (argv, "Terminal")
    def choose_iterm(dialog):  # the user picks iTerm2 on the Terminal page of Preferences and saves
        dialog.terminal_panel.terminal.setCurrentText("iTerm2")
        dialog._save()
        return dialog.result()
    monkeypatch.setattr(PreferencesDialog, "exec", choose_iterm)
    window.open_preferences()
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
