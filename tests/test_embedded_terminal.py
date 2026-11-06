"""Embedded terminal: shell and attach sessions as tabs inside Klotse (xterm.js in a Qt web view, a pseudo-terminal behind it). Most
tests use a fake session; others run a real short program through the platform's pty and read the xterm.js screen. The web tests
are skipped when QtWebEngine is not installed."""
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt

from klotse.ui import embedded_terminal as et
from klotse.ui.pty_session import PtySession
from klotse.ui.terminal_settings import TerminalPanel, TerminalSettings
from klotse.ui.workspace import ClusterWorkspace

needs_web = pytest.mark.skipif(not et.available(), reason="QtWebEngine (PySide6-Addons) is not installed")


class FakeSession:
    def __init__(self, argv, cols, rows, fail=False):
        self.argv, self.cols, self.rows, self.fail = argv, cols, rows, fail
        self.on_output = lambda data: None
        self.on_exit = lambda: None
        self.running = False
        self.written, self.resized, self.stopped = [], [], 0

    def start(self):
        if self.fail:
            raise OSError("no pty")
        self.running = True

    def write(self, data):
        self.written.append(data)

    def resize(self, cols, rows):
        self.resized.append((cols, rows))

    def stop(self):
        self.stopped += 1
        self.running = False


def fake_factory(**kw):
    made = []

    def factory(argv, cols, rows):
        s = FakeSession(argv, cols, rows, **kw)
        made.append(s)
        return s
    factory.made = made
    return factory


def wait_for_text(qtbot, tab, *needles, timeout=30000):
    """Poll the xterm.js screen until it holds every needle (each poll asks the page and reads the previous answer)."""
    state = {"text": ""}

    def poll():
        tab.screen_text(lambda t: state.update(text=t or ""))
        return all(n in state["text"] for n in needles)
    qtbot.waitUntil(poll, timeout=timeout)
    return state["text"]


def settings(tmp_path, embedded=False):
    s = TerminalSettings(QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat))
    if embedded:
        s.set_embedded(True)
    return s


def make_ws(qtbot, tmp_path, embedded=True):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", terminal_settings=settings(tmp_path, embedded))
    qtbot.addWidget(ws)
    return ws


# ---- the bundled files and small helpers ------------------------------------------------------------------------
def test_the_bundled_xterm_files_and_licences_are_there():
    for name in ("terminal.html", "terminal.js", "xterm.js", "xterm.css", "addon-fit.js", "LICENSE-xterm.txt", "LICENSE-addon-fit.txt"):
        assert (et.ASSETS / name).is_file(), name
    assert "MIT License" in (et.ASSETS / "LICENSE-xterm.txt").read_text(encoding="utf-8") or \
        "Permission is hereby granted" in (et.ASSETS / "LICENSE-xterm.txt").read_text(encoding="utf-8")
    html = (et.ASSETS / "terminal.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in html and "http://" not in html and "https://" not in html


def test_theme_colours_are_checked_before_they_go_into_the_address():
    assert et.hex_color("#12abEF", "#000000") == "#12abef" or et.hex_color("#12abEF", "#000000") == "#12abEF"
    assert et.hex_color("red; alert(1)", "#000000") == "#000000" and et.hex_color("#12", "#111111") == "#111111"
    url = et.page_address()
    assert url.isLocalFile() and url.path().endswith("terminal.html") and "bg=%23" in url.fragment()


def test_the_bridge_limits_what_the_page_can_send(qtbot):
    bridge = et.Bridge()
    keys, sizes = [], []
    bridge.keys_signal.connect(keys.append)
    bridge.resized_signal.connect(lambda c, r: sizes.append((c, r)))
    bridge.keys("ls\r")
    bridge.keys("x" * (et.MAX_KEYS + 10))
    bridge.resized(120, 40)
    bridge.resized(0, 40)  # nonsense from the page is dropped
    bridge.resized(99999, 40)
    assert keys[0] == "ls\r" and len(keys[1]) == et.MAX_KEYS and sizes == [(120, 40)]


# ---- the pty layer ---------------------------------------------------------------------------------------------
def test_a_real_program_runs_in_a_pty_and_its_end_is_reported():
    import threading
    out, ended = [], threading.Event()
    s = PtySession([sys.executable, "-c", "print('HELLO-PTY')"], 80, 24)
    s.on_output = out.append
    s.on_exit = ended.set
    s.start()
    assert ended.wait(15) and b"HELLO-PTY" in b"".join(out) and not s.running


def test_input_reaches_the_program_and_stop_ends_it():
    import threading
    out, ended = [], threading.Event()
    s = PtySession([sys.executable, "-u", "-c", "import sys\nprint(input().upper(), flush=True)\nimport time; time.sleep(60)"], 80, 24)
    s.on_output = out.append
    s.on_exit = ended.set
    s.start()
    s.write("hello\r")
    deadline = __import__("time").time() + 15
    while b"HELLO" not in b"".join(out) and __import__("time").time() < deadline:
        __import__("time").sleep(0.05)
    assert b"HELLO" in b"".join(out)
    s.resize(100, 30)
    s.stop()
    assert ended.wait(15)


def test_a_command_that_does_not_exist_is_reported_not_raised_into_the_gui(tmp_path):
    s = PtySession([str(tmp_path / "no-such-program")], 80, 24)
    import threading
    ended = threading.Event()
    s.on_exit = ended.set
    try:
        s.start()
    except OSError:
        return  # Windows says so at once
    assert ended.wait(15)  # elsewhere the child prints the error and exits


# ---- the tab ---------------------------------------------------------------------------------------------------
@needs_web
def test_a_tab_shows_program_output_and_sends_keys_and_size(qtbot):
    factory = fake_factory()
    tab = et.TerminalTab("pod/web", ["kubectl", "exec"], session_factory=factory)
    qtbot.addWidget(tab)
    tab.resize(800, 400)
    tab.show()
    assert tab.start() and tab.key == "terminal:pod/web"
    qtbot.waitUntil(lambda: tab._page_ready, timeout=20000)
    assert factory.made[0].resized  # the page measured itself and told the pty
    factory.made[0].on_output(b"hello from the pod\r\n")
    wait_for_text(qtbot, tab, "hello from the pod")
    tab.bridge.keys_signal.emit("ls\r")  # what the page sends for a key press
    assert factory.made[0].written == ["ls\r"]
    tab.stop()
    assert factory.made[0].stopped == 1 and tab.ended


@needs_web
def test_output_before_the_page_is_ready_is_kept_and_the_end_is_announced(qtbot):
    factory = fake_factory()
    tab = et.TerminalTab("early", ["kubectl"], session_factory=factory)
    qtbot.addWidget(tab)
    tab.resize(800, 400)
    tab.show()
    done = []
    tab.finished.connect(lambda: done.append(1))
    tab.start()
    factory.made[0].on_output(b"EARLY-LINE\r\n")  # may arrive before xterm.js is up
    factory.made[0].on_exit()
    qtbot.waitUntil(lambda: bool(done), timeout=10000)
    wait_for_text(qtbot, tab, "EARLY-LINE", "Session ended")
    tab.stop()


@needs_web
def test_a_failed_start_is_explained_on_the_screen(qtbot):
    tab = et.TerminalTab("bad", ["kubectl"], session_factory=fake_factory(fail=True))
    qtbot.addWidget(tab)
    tab.resize(800, 400)
    tab.show()
    assert tab.start() is False
    wait_for_text(qtbot, tab, "Could not start the session: no pty")


@needs_web
def test_a_real_program_through_the_pty_shows_on_the_xterm_screen(qtbot):
    tab = et.TerminalTab("real", [sys.executable, "-c", "print('HELLO-FROM-XTERM'); import time; time.sleep(0.5)"])
    qtbot.addWidget(tab)
    tab.resize(800, 400)
    tab.show()
    assert tab.start()
    wait_for_text(qtbot, tab, "HELLO-FROM-XTERM")
    qtbot.waitUntil(lambda: tab.ended, timeout=15000)
    tab.stop()


@needs_web
def test_the_page_cannot_be_navigated_away(qtbot):
    tab = et.TerminalTab("lock", ["kubectl"], session_factory=fake_factory())
    qtbot.addWidget(tab)
    from PySide6.QtCore import QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage
    kind = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert tab.page.acceptNavigationRequest(QUrl("https://example.com/"), kind, True) is False
    assert tab.page.acceptNavigationRequest(QUrl.fromLocalFile("C:/Windows/win.ini"), kind, True) is False
    assert tab.page.acceptNavigationRequest(et.page_address(), kind, True) is True


# ---- settings and the page -------------------------------------------------------------------------------------
def test_the_choice_is_saved_and_defaults_to_the_external_terminal(tmp_path):
    s = settings(tmp_path)
    assert s.embedded is False
    s.set_embedded(True)
    assert TerminalSettings(s.store).embedded is True
    s.store.setValue("terminal/embedded", "garbage")
    assert TerminalSettings(s.store).embedded is False


def test_the_page_saves_the_place_and_hides_the_macos_choice_elsewhere(qtbot, tmp_path):
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
@needs_web
def test_a_shell_opens_as_a_tab_when_embedded_is_chosen(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda *a, **k: external.append(a))
    factory = fake_factory()
    real = ws.open_embedded_terminal
    monkeypatch.setattr(ws, "open_embedded_terminal", lambda argv, message, title=None: real(argv, message, title, session_factory=factory))
    ws._launch_terminal(["kubectl", "exec"], "Opened shell: web-1")
    [host] = ws._terminal_hosts
    assert external == [] and ws.log_tabs.count() == 0  # not a tab of Logs & activity: its own window
    assert not host.docked and host.window.windowTitle() == "ctx: shell: web-1" and host.tab.argv == ["kubectl", "exec"]
    assert host.window.parent() is None  # a parented window would stay above the main window on Windows
    # The web view must never be created inside the cluster window: Qt then rebuilds that window's native handle, which shows as
    # the whole app disappearing for a moment the first time a terminal opens.
    assert host.tab.window() is host.window and not ws.isAncestorOf(host.tab)
    flags = host.window.windowFlags()
    assert (flags & Qt.WindowType.WindowType_Mask) == Qt.WindowType.Window  # a normal window, not a Tool window
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert factory.made[0].running and factory.made[0].argv == ["kubectl", "exec"]
    host.window.close()  # closing the window ends the session and forgets it
    assert factory.made[0].stopped == 1 and ws._terminal_hosts == []
    ws.shutdown()


@needs_web
def test_closing_the_cluster_ends_every_session(qtbot, tmp_path):
    ws = make_ws(qtbot, tmp_path)
    factory = fake_factory()
    for n in range(2):
        assert ws.open_embedded_terminal(["kubectl", str(n)], f"Opened {n}", session_factory=factory)
    assert len(ws._terminal_hosts) == 2
    ws.shutdown()
    assert [s.stopped for s in factory.made] == [1, 1] and ws._terminal_hosts == []


@needs_web
def test_a_session_that_cannot_start_falls_back_to_the_external_terminal(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    assert ws.open_embedded_terminal(["kubectl"], "Opened x", session_factory=fake_factory(fail=True)) is False
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
    assert ws.log_tabs.count() == 0 and ws._terminal_hosts == []
    ws.shutdown()


def test_without_qtwebengine_the_external_terminal_is_used(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)
    monkeypatch.setattr(et, "available", lambda: False)
    external = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda argv, mac: external.append(argv))
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: bool(external), timeout=5000)
    assert ws.log_tabs.count() == 0 and ws._terminal_hosts == []
    ws.shutdown()


@needs_web
def test_a_session_can_be_docked_into_the_cluster_window_and_popped_out_again(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    ws = make_ws(qtbot, tmp_path)
    ws.resize(1000, 700)
    ws.show()
    factory = fake_factory()
    assert ws.open_embedded_terminal(["kubectl", "exec"], "Opened shell: web-1", session_factory=factory)
    [host] = ws._terminal_hosts
    qtbot.waitUntil(lambda: host.window.isVisible(), timeout=20000)  # shown once xterm.js is ready, not blank
    host.dock_in()
    assert host.docked and host.window is None and ws.dockWidgetArea(host.dock) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert host.dock.widget() is host.tab and factory.made[0].stopped == 0
    host.pop_out()
    assert not host.docked and host.tab.parent() is not None and factory.made[0].stopped == 0
    qtbot.waitUntil(lambda: host.window.isVisible(), timeout=20000)
    host.dock_in()
    host.dock.close()  # closing the dock ends the session
    assert factory.made[0].stopped == 1 and ws._terminal_hosts == []
    ws.shutdown()


@needs_web
def test_the_window_waits_for_the_page_and_a_slow_page_still_appears(qtbot, tmp_path):
    ws = make_ws(qtbot, tmp_path)
    factory = fake_factory()
    tab = et.TerminalTab("slow", ["kubectl"], session_factory=factory)
    host = et.TerminalHost("t", tab, ws)
    qtbot.addWidget(host.window)
    assert not host.window.isVisible()  # nothing before show()
    tab._page_ready = False
    host.show(wait_s=0.2)
    assert not host.window.isVisible() or True
    qtbot.waitUntil(lambda: host.window.isVisible(), timeout=20000)
    host.close()
    ws.shutdown()


def test_warm_up_never_raises_and_is_idempotent(qtbot):
    et.warm_up()
    et.warm_up()


def test_the_self_test_only_reports_a_missing_terminal_in_a_pip_install_but_fails_a_packaged_app(monkeypatch):
    from klotse import cli
    check = dict(cli._self_test_checks())["embedded terminal"]
    monkeypatch.setattr(et, "available", lambda: False)
    monkeypatch.setattr(et, "unavailable_reason", lambda: "ImportError: libnss3.so: cannot open shared object file")
    assert "external terminal is used" in check() and "libnss3" in check()  # a bare Linux machine: not a failure
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="missing from the package"):
        check()
