"""Support code for packaged (frozen) builds: command-line flags, the macOS terminal runner and PATH."""
import json
import os
import shlex
import subprocess
import sys

import pytest

import klotse
from klotse import cli
from klotse.ui import environment, terminal, terminal_session


# ---- command line ---------------------------------------------------------------------------------------------
def test_version_flag_prints_and_exits_without_starting_the_gui(capsys):
    assert cli.handle(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"Klotse {klotse.__version__}"
    assert cli.handle(["-V"]) == 0


def test_other_arguments_are_left_to_the_gui():
    assert cli.handle([]) is None
    assert cli.handle(["-style", "fusion"]) is None  # Qt options pass through


def test_self_test_passes_here_and_writes_a_report_file(qtbot, tmp_path, capsys):
    report = tmp_path / "report.txt"
    assert cli.handle(["--self-test", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    for name in ("version", "mode", "imports", "qt", "logo", "about dialog", "kubectl"):
        assert f"{name}:" in text
    assert "FAIL" not in text and text.rstrip().endswith("RESULT: OK")
    assert capsys.readouterr().out == ""  # nothing on stdout when a file was given


def test_self_test_reports_every_failure_and_fails(qtbot, monkeypatch, capsys):
    def broken():
        return [("good", lambda: "fine"), ("bad", lambda: 1 / 0), ("also good", lambda: "fine")]

    monkeypatch.setattr(cli, "_self_test_checks", broken)
    assert cli.self_test() == 1
    out = capsys.readouterr().out
    assert "ok    good: fine" in out and "ok    also good: fine" in out  # one failure does not hide the rest
    assert "FAIL  bad: ZeroDivisionError" in out and out.rstrip().endswith("RESULT: FAILED")


def test_self_test_survives_a_missing_console_and_a_legacy_encoding(qtbot, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_self_test_checks", lambda: [("odd", lambda: "caf\u00e9 \u2026")])
    assert cli.self_test() == 0
    assert "caf? ?" in capsys.readouterr().out  # non-ASCII is replaced, not raised
    monkeypatch.setattr(sys, "stdout", None)  # a windowed Windows build
    assert cli.self_test() == 0


def test_the_module_runs_as_a_program():
    result = subprocess.run([sys.executable, "-m", "klotse", "--version"], capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, timeout=60)
    assert result.returncode == 0 and result.stdout.strip() == f"Klotse {klotse.__version__}"


# ---- the macOS terminal session in a packaged app ---------------------------------------------------------------
def test_terminal_session_flag_runs_the_session(monkeypatch):
    seen = []
    monkeypatch.setattr(terminal_session, "run_session", lambda command, app, target: seen.append((command, app, target)) or 7)
    status = cli.handle(["--terminal-session", "iTerm2", json.dumps(["kubectl", "get", "pods"]), "42", "session-id"])
    assert status == 7 and seen == [(["kubectl", "get", "pods"], "iTerm2", ["42", "session-id"])]


def test_terminal_session_flag_needs_its_arguments(capsys):
    assert cli.handle(["--terminal-session", "Terminal"]) == 2
    assert "usage" in capsys.readouterr().err


def test_frozen_app_runs_the_session_through_its_own_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Klotse.app/Contents/MacOS/Klotse")
    command = terminal._mac_session_command(["kubectl", "exec", "-it", "p"], "Terminal")
    assert command.startswith("exec /Applications/Klotse.app/Contents/MacOS/Klotse --terminal-session Terminal ")
    assert "terminal_session.py" not in command  # there is no script file in a packaged app
    assert json.loads(shlex.split(command)[4]) == ["kubectl", "exec", "-it", "p"]


def test_source_run_still_uses_the_script(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    parts = shlex.split(terminal._mac_session_command(["kubectl"], "Terminal"))
    assert parts[:2] == ["exec", sys.executable] and parts[2].endswith("terminal_session.py")


# ---- PATH for apps started from Finder --------------------------------------------------------------------------
P = os.pathsep  # production runs this on macOS (":"), the tests also run on Windows (";")


def test_merged_path_keeps_order_adds_only_existing_dirs_and_no_repeats():
    exists = {"/opt/homebrew/bin", "/usr/local/bin"}.__contains__
    merged = environment.merged_path(P.join(["/usr/bin", "/bin"]),
                                     ["/opt/homebrew/bin", "/nope", "/usr/bin", "/usr/local/bin"], exists)
    assert merged == P.join(["/usr/bin", "/bin", "/opt/homebrew/bin", "/usr/local/bin"])
    assert environment.merged_path("", ["/opt/homebrew/bin"], exists) == "/opt/homebrew/bin"
    assert environment.merged_path(P.join(["/a", "/b"]), [], lambda p: False) == P.join(["/a", "/b"])  # nothing dropped


def test_login_shell_path_extracts_the_path_between_markers():
    banner = "Welcome!\nloading rc...\n"

    def run(cmd, **kw):
        assert cmd[0] == "/bin/zsh" and "-ilc" in cmd and kw["timeout"] == 3
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{banner}{environment._MARKER}/a:/b{environment._MARKER}\n")

    assert environment.login_shell_path("/bin/zsh", run=run) == "/a:/b"


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("zsh", 3), FileNotFoundError(), PermissionError()])
def test_login_shell_path_never_raises(failure):
    def run(cmd, **kw):
        raise failure

    assert environment.login_shell_path("/bin/zsh", run=run) == ""
    assert environment.login_shell_path("/bin/zsh", run=lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="junk")) == ""


def test_prepare_environment_does_nothing_off_macos():
    env = {"PATH": P.join(["/usr/bin", "/bin"])}
    for platform in ("win32", "linux"):
        environment.prepare_environment(platform, env, interactive=False, shell_path=lambda: pytest.fail("not on this OS"))
    assert env["PATH"] == P.join(["/usr/bin", "/bin"])


def test_prepare_environment_from_a_terminal_does_not_ask_the_shell():
    env = {"PATH": P.join(["/usr/bin", "/bin"])}
    environment.prepare_environment("darwin", env, interactive=True, shell_path=lambda: pytest.fail("PATH is already right"))
    entries = env["PATH"].split(P)
    assert entries[:2] == ["/usr/bin", "/bin"]  # original entries first; the usual tool directories only if they exist
    assert all(entry in ("/usr/bin", "/bin") or os.path.isdir(entry) for entry in entries)


def test_prepare_environment_adds_directories_from_the_login_shell(tmp_path):
    shell_dir = tmp_path / "shellbin"
    shell_dir.mkdir()
    env = {"PATH": "/usr/bin"}
    environment.prepare_environment("darwin", env, interactive=False,
                                    shell_path=lambda: P.join(["/usr/bin", str(shell_dir), str(tmp_path / "missing")]))
    entries = env["PATH"].split(P)
    assert entries[0] == "/usr/bin" and str(shell_dir) in entries and str(tmp_path / "missing") not in entries
    assert entries.count("/usr/bin") == 1
