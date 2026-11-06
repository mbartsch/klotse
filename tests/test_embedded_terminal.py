"""Embedded terminal: shell and attach sessions as tabs inside Klotse, run by termqt. Most tests use a fake pseudo-terminal; one runs
a real short program through the platform's pty to prove the whole path (skipped when termqt is not installed)."""
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.ui import embedded_terminal as et
from klotse.ui.terminal_settings import TerminalPanel, TerminalSettings
from klotse.ui.workspace import ClusterWorkspace

needs_termqt = pytest.mark.skipif(not et.available(), reason="termqt is not installed")


class FakeIO:
    def __init__(self, argv, cols, rows, fail=False):
        self.argv, self.cols, self.rows, self.fail = argv, cols, rows, fail
        self.running = False
        self.written, self.resized, self.terminated = [], [], 0
        self.stdout_callback = lambda b: None
        self.terminated_callback = lambda: None

    def spawn(self):
        if self.fail:
            raise OSError("no pty")
        self.running = True

    def write(self, data):
        self.written.append(data)

    def resize(self, rows, cols):
        self.resized.append((rows, cols))

    def terminate(self):
        self.terminated += 1
        self.running = False


def fake_factory(**kw):
    made = []

    def factory(argv, cols, rows):
        io = FakeIO(argv, cols, rows, **kw)
        made.append(io)
        return io
    factory.made = made
    return factory


def settings(tmp_path, embedded=False):
    s = TerminalSettings(QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat))
    if embedded:
        s.set_embedded(True)
    return s


def make_ws(qtbot, tmp_path, embedded=True):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", terminal_settings=settings(tmp_path, embedded))
    qtbot.addWidget(ws)
    return ws


# ---- helpers ---------------------------------------------------------------------------------------------------
def test_command_text_quotes_for_the_posix_splitter():
    import shlex
    argv = ["kubectl", "--context", "my ctx", "exec", "-it", "p", "--", "sh"]
    assert shlex.split(et.command_text(argv)) == argv


def test_the_windows_and_posix_ios_are_the_right_classes():
    io = et.make_io(["kubectl", "get", "pods"], 80, 24)
    assert hasattr(io, "spawn") and io.cols == 80 and io.rows == 24
    if sys.platform == "win32":
        assert io.cmd == ["kubectl", "get", "pods"]  # an argv list: winpty mangles a quoted string
    else:
        assert io.cmd == "kubectl get pods"


# ---- the tab ---------------------------------------------------------------------------------------------------
@needs_termqt
def test_a_tab_connects_keys_output_and_resizing_to_the_process(qtbot):
    factory = fake_factory()
    tab = et.TerminalTab("pod/web", ["kubectl", "exec"], io_factory=factory)
    qtbot.addWidget(tab)
    assert tab.start() and factory.made[0].running and tab.key == "terminal:pod/web"
    tab.terminal.input(b"ls\r")  # what a key press sends
    assert factory.made[0].written == [b"ls\r"]
    tab.terminal.resize_callback(30, 90)
    assert factory.made[0].resized == [(30, 90)]
    tab.io.stdout_callback(b"hello from the pod\r\n")
    qtbot.waitUntil(lambda: "hello from the pod" in tab.screen_text(), timeout=3000)
    tab.stop()
    assert factory.made[0].terminated == 1 and tab.ended


@needs_termqt
def test_a_process_that_ends_says_so_and_a_failed_start_is_reported(qtbot):
    factory = fake_factory()
    tab = et.TerminalTab("x", ["kubectl"], io_factory=factory)
    qtbot.addWidget(tab)
    tab.start()
    done = []
    tab.finished.connect(lambda: done.append(1))
    factory.made[0].terminated_callback()  # called from the reader thread in real life
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert tab.ended and not tab.status.isHidden() and "ended" in tab.status.text()

    bad = et.TerminalTab("y", ["kubectl"], io_factory=fake_factory(fail=True))
    qtbot.addWidget(bad)
    assert bad.start() is False and "Could not start" in bad.status.text()


@needs_termqt
def test_a_real_program_runs_through_the_pty_and_shows_on_screen(qtbot):
    tab = et.TerminalTab("real", [sys.executable, "-c", "print('HELLO-FROM-PTY'); import time; time.sleep(0.5)"])
    qtbot.addWidget(tab)
    tab.show()
    assert tab.start()
    qtbot.waitUntil(lambda: "HELLO-FROM-PTY" in tab.screen_text(), timeout=15000)
    qtbot.waitUntil(lambda: tab.ended, timeout=15000)
    tab.stop()


# ---- settings and the page -------------------------------------------------------------------------------------
def test_the_choice_is_saved_and_defaults_to_the_external_terminal(tmp_path):
    s = settings(tmp_path)
    assert s.embedded is False
    s.set_embedded(True)
    assert TerminalSettings(s.store).embedded is True
    s.store.setValue("terminal/embedded", "garbage")
    assert TerminalSettings(s.store).embedded is False


def test_the_page_saves_the_place_and_hides_the_macos_choice_elsewhere(qtbot, tmp_path, monkeypatch):
    s = settings(tmp_path)
    panel = TerminalPanel(s)
    qtbot.addWidget(panel)
    panel.where.setCurrentIndex(1)
    assert s.embedded is False  # choosing is not saving
    panel.save()
    assert s.embedded is True
    if sys.platform != "darwin":
        assert panel.terminal.isHidden() and panel.mac_hint.isHidden()


# ---- the workspace ---------------------------------------------------------------------------------------------
@needs_termqt
def test_a_shell_opens_as_a_tab_when_embedded_is_chosen(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda *a, **k: external.append(a))
    factory = fake_factory()
    real = ws.open_embedded_terminal
    monkeypatch.setattr(ws, "open_embedded_terminal", lambda argv, message, title=None: real(argv, message, title, io_factory=factory))
    ws._launch_terminal(["kubectl", "exec"], "Opened shell: web-1")
    assert external == [] and ws.log_tabs.count() == 1 and ws.log_tabs.tabText(0) == "Shell: shell: web-1"
    assert factory.made[0].running and factory.made[0].argv == ["kubectl", "exec"]
    ws._close_log_tab(0)
    assert factory.made[0].terminated == 1
    ws.shutdown()


@needs_termqt
def test_closing_the_cluster_ends_every_session(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    factory = fake_factory()
    for n in range(2):
        assert ws.open_embedded_terminal(["kubectl", str(n)], f"Opened {n}", io_factory=factory)
    ws.shutdown()
    assert [io.terminated for io in factory.made] == [1, 1]


@needs_termqt
def test_a_session_that_cannot_start_falls_back_to_the_external_terminal(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    assert ws.open_embedded_terminal(["kubectl"], "Opened x", io_factory=fake_factory(fail=True)) is False
    assert ws.log_tabs.count() == 0
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda argv, mac: external.append(argv))
    monkeypatch.setattr(ws, "open_embedded_terminal", lambda *a, **k: False)
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: bool(external), timeout=5000)
    ws.shutdown()


def test_the_default_still_uses_the_external_terminal(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path, embedded=False)
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda argv, mac: external.append(argv))
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: bool(external), timeout=5000)
    assert ws.log_tabs.count() == 0
    ws.shutdown()


def test_without_termqt_the_external_terminal_is_used(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    monkeypatch.setattr(et, "available", lambda: False)
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda argv, mac: external.append(argv))
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: bool(external), timeout=5000)
    assert ws.log_tabs.count() == 0
    ws.shutdown()
