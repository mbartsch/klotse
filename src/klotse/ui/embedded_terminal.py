"""Shell and attach sessions inside the Klotse window: xterm.js (MIT, the engine behind VS Code's terminal) in a Qt web view, joined to
a pseudo-terminal (`pty_session.PtySession`) by a QWebChannel. A session is a tab in the Logs & activity dock running the same
explicit-context kubectl command the external terminals run. QtWebEngine (PySide6-Addons) is needed; without it `ClusterWorkspace`
opens the external terminal as before. Output from the cluster only ever reaches the page as bytes handed to `term.write`, never as
HTML, and the page cannot navigate away from the bundled files."""
import base64
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import theme
from .pty_session import PtySession

# A terminal needs no GPU: software rendering avoids driver trouble and the GPU process's memory. Read when QtWebEngine starts.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "xterm"
HEX = re.compile(r"#[0-9a-fA-F]{6}")
MAX_PASTE = 1_000_000  # characters; a paste larger than this is cut (a runaway clipboard must not flood a shell)
MAX_KEYS = 1_000_000


def available():
    """True when QtWebEngine and the bundled xterm.js files can be used (and pywinpty on Windows)."""
    try:
        if not (ASSETS / "terminal.html").is_file() or not (ASSETS / "xterm.js").is_file():
            return False
        from PySide6.QtWebChannel import QWebChannel  # noqa: F401
        from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        if sys.platform == "win32":
            import winpty  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - an import error or a missing DLL; never fatal
        return False


def hex_color(value, fallback):
    """A '#rrggbb' text for a theme colour (QColor or text); the fallback when it is anything else."""
    text = value.name() if hasattr(value, "name") else str(value)
    return text if HEX.fullmatch(text) else fallback


def page_address(font_size=13):
    """The terminal page with the theme's colours in its fragment (each is checked to be #rrggbb here and again in the page)."""
    params = {"bg": hex_color(theme.BASE, "#0f1b1f"), "fg": hex_color(theme.TEXT, "#e6edf0"),
              "sel": hex_color(theme.ACCENT, "#3d6670"), "cur": hex_color(theme.TEXT, "#e6edf0"), "size": str(int(font_size))}
    url = QUrl.fromLocalFile(str(ASSETS / "terminal.html"))
    url.setFragment(urlencode(params))
    return url


class Bridge(QObject):
    """What the page and Python say to each other. Only fixed slots; page input is typed and size-limited."""
    output = Signal(str)  # base64 of program output -> term.write
    notice = Signal(str)  # a line shown dimmed in the terminal ("Session ended")
    pasteText = Signal(str)  # clipboard text -> term.paste
    ready_signal = Signal(int, int)
    keys_signal = Signal(str)
    resized_signal = Signal(int, int)

    @Slot(int, int)
    def ready(self, cols, rows):
        self.ready_signal.emit(int(cols), int(rows))

    @Slot(str)
    def keys(self, data):
        self.keys_signal.emit(str(data)[:MAX_KEYS])

    @Slot(int, int)
    def resized(self, cols, rows):
        if 2 <= cols <= 1000 and 2 <= rows <= 1000:
            self.resized_signal.emit(int(cols), int(rows))

    @Slot(str)
    def copy(self, text):
        QGuiApplication.clipboard().setText(str(text)[:MAX_PASTE])

    @Slot()
    def requestPaste(self):
        self.pasteText.emit((QGuiApplication.clipboard().text() or "")[:MAX_PASTE])


def make_session(argv, cols, rows):
    return PtySession(argv, cols, rows)


class TerminalTab(QWidget):
    """One session. `key` marks it for the dock; closing the tab calls `stop()`, which ends the process (so no kubectl outlives its
    tab). `session_factory(argv, cols, rows)` is replaceable for tests. `finished` fires once, when the process has ended."""
    finished = Signal()
    _program_output = Signal(bytes)
    _program_exited = Signal()
    COLS, ROWS = 100, 28

    def __init__(self, title, argv, session_factory=make_session, parent=None):
        super().__init__(parent)
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class LockedPage(QWebEnginePage):
            def acceptNavigationRequest(self, url, kind, is_main_frame):
                return url.isLocalFile() and url.path().endswith("terminal.html")  # nothing else, ever

        self.key = f"terminal:{title}"
        self.title = title
        self.argv = list(argv)
        self.ended = False
        self._error = ""
        self._page_ready = False
        self._pending = []  # output that arrived before the page was ready
        self.session = session_factory(self.argv, self.COLS, self.ROWS)
        self.session.on_output = self._program_output.emit  # from the reader thread; queued onto the GUI thread
        self.session.on_exit = self._program_exited.emit
        self._program_output.connect(self._show_output)
        self._program_exited.connect(self._show_ended)

        self.bridge = Bridge(self)
        self.bridge.ready_signal.connect(self._on_ready)
        self.bridge.keys_signal.connect(self.session.write)
        self.bridge.resized_signal.connect(self.session.resize)
        self.view = QWebEngineView(self)
        self.page = LockedPage(self.view)
        settings = self.page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)  # reads xterm.js beside it
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.setUrl(page_address())

    # ---- running ----------------------------------------------------------------------------------------------
    def start(self):
        """Start the process; False when the pseudo-terminal cannot be created (the page then says why)."""
        try:
            self.session.start()
        except Exception as e:  # noqa: BLE001 - whatever the platform's pty layer raises
            self.ended = True
            self._error = f"Could not start the session: {e}"
            return False
        return True

    def _on_ready(self, cols, rows):
        self._page_ready = True
        self.session.resize(cols, rows)
        for chunk in self._pending:
            self.bridge.output.emit(base64.b64encode(chunk).decode("ascii"))
        self._pending.clear()
        if self._error:
            self.bridge.notice.emit(self._error)
        elif self.ended:
            self.bridge.notice.emit("Session ended. Close this tab.")

    def _show_output(self, data):
        if self._page_ready:
            self.bridge.output.emit(base64.b64encode(data).decode("ascii"))
        else:
            self._pending.append(data)

    def _show_ended(self):
        self.ended = True
        if self._page_ready:
            self.bridge.notice.emit("Session ended. Close this tab.")
        self.finished.emit()

    def run_script(self, script, callback=None):
        """Run JavaScript in the terminal page (tests, and `screen_text`)."""
        if callback is None:
            self.page.runJavaScript(script)
        else:
            self.page.runJavaScript(script, 0, callback)

    def screen_text(self, callback):
        """Everything the terminal holds (screen and scrollback) as text, passed to `callback`."""
        self.run_script("(function(){if(typeof term==='undefined')return '';const b=term.buffer.active;let t=[];for(let i=0;i<b.length;i++)"
                        "t.push(b.getLine(i).translateToString(true));return t.join('\\n').trimEnd();})()", callback)

    def focus_terminal(self):
        self.view.setFocus()
        self.run_script("term.focus()")

    def stop(self):
        """Called when the tab is closed or the cluster is: end the process."""
        self.ended = True
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
