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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .pty_session import PtySession

# A terminal needs no GPU: software rendering avoids driver trouble and the GPU process's memory. Read when QtWebEngine starts.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ASSETS = Path(__file__).resolve().parent.parent / "assets" / "xterm"
HEX = re.compile(r"#[0-9a-fA-F]{6}")
MAX_PASTE = 1_000_000  # characters; a paste larger than this is cut (a runaway clipboard must not flood a shell)
MAX_KEYS = 1_000_000


def unavailable_reason():
    """'' when the embedded terminal can be used, else a short reason (what failed to import or is missing)."""
    try:
        if not (ASSETS / "terminal.html").is_file() or not (ASSETS / "xterm.js").is_file():
            return "the bundled xterm.js files are missing"
        from PySide6.QtWebChannel import QWebChannel  # noqa: F401
        from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        if sys.platform == "win32":
            import winpty  # noqa: F401
        return ""
    except Exception as e:  # noqa: BLE001 - an import error or a missing DLL/system library; never fatal
        return f"{type(e).__name__}: {e}"[:200]


def available():
    """True when QtWebEngine and the bundled xterm.js files can be used (and pywinpty on Windows)."""
    return not unavailable_reason()


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
    page_ready = Signal()  # xterm.js is up and has measured itself
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
        # the terminal's own colour from the first frame: a web view is white until its page paints, which showed as a white flash
        self.page.setBackgroundColor(QColor(hex_color(theme.BASE, "#0f1b1f")))
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(hex_color(theme.BASE, "#0f1b1f")))
        self.setPalette(palette)
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
        self.page_ready.emit()
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


class TerminalHost(QObject):
    """Where one session lives: an ordinary top-level window (own taskbar entry, never forced above other windows) or, after
    *Dock in Klotse window*, a dock inside the cluster window; *Pop out* goes back. Moving between the two only re-parents the
    web view, so the session, its scrollback and the running program are untouched. Closing either ends the session."""
    closed = Signal()

    def __init__(self, title, tab, workspace):
        super().__init__(workspace)
        self.title, self.tab, self.workspace = title, tab, workspace
        self.window = None
        self.dock = None
        self._closing = False
        self._make_window()

    # ---- the two homes ----------------------------------------------------------------------------------------
    def _bar(self, label, button_text, handler):
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 2, 2, 2)
        text = QLabel(label)
        theme.muted(text)
        button = QPushButton(button_text)
        button.setFlat(True)
        button.clicked.connect(handler)
        row.addWidget(text, 1)
        row.addWidget(button)
        return bar

    def _make_window(self):
        class Window(QWidget):
            def closeEvent(w, event):  # noqa: N805
                self._end()
                event.accept()
        window = Window()  # no parent: a parented window stays above its owner on Windows
        window.setWindowTitle(self.title)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar("Drag-free docking: use the button to put this terminal inside the Klotse window.",
                                   "Dock in Klotse window", self.dock_in))
        layout.addWidget(self.tab, 1)
        self.window = window
        self.dock = None

    def show(self, near=None, wait_s=4.0):
        """Show the window (first time: near `near`, a QWidget). It appears once xterm.js is ready, so no blank window flashes
        up and the main window is not disturbed while the page loads; after `wait_s` seconds it is shown anyway."""
        window = self.window
        window.resize(900, 560)
        if near is not None:
            window.move(near.mapToGlobal(near.rect().topLeft()) + QPoint(80, 80))
        done = []

        def reveal():
            if done or window is not self.window or self._closing:
                return
            done.append(1)
            window.show()
            window.raise_()
            window.activateWindow()
            self.tab.focus_terminal()
        if self.tab._page_ready:
            reveal()
        else:
            self.tab.page_ready.connect(reveal)
            QTimer.singleShot(int(wait_s * 1000), reveal)

    def dock_in(self):
        """Move the session from its own window into a dock of the cluster window."""
        if self.window is None or self._closing:
            return
        window, self.window = self.window, None
        dock = QDockWidget(self.title, self.workspace)
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setTitleBarWidget(self._bar(self.title, "Pop out to a window", self.pop_out))
        dock.setWidget(self.tab)
        dock.installEventFilter(self)
        self.dock = dock
        self.workspace.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.show()
        window.removeEventFilter(self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        window.hide()
        window.deleteLater()
        self.tab.focus_terminal()

    def pop_out(self):
        """Move the session from the dock back into its own window."""
        if self.dock is None or self._closing:
            return
        dock, self.dock = self.dock, None
        dock.removeEventFilter(self)
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.tab.setParent(None)
        self.workspace.removeDockWidget(dock)
        dock.deleteLater()
        self._make_window()
        self.show(near=self.workspace)

    @property
    def docked(self):
        return self.dock is not None

    # ---- ending ---------------------------------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj is self.dock and event.type() == QEvent.Type.Close:
            self._end()
        return False

    def _end(self):
        if self._closing:
            return
        self._closing = True
        self.tab.stop()
        self.closed.emit()

    def close(self):
        """End the session and close whichever home it has (used when the cluster closes)."""
        self._end()
        if self.window is not None:
            self.window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.window.close()
            self.window = None
        if self.dock is not None:
            self.dock.close()
            self.dock = None


_warm = None
WARM_SHOWN_MS = 2500  # how long the invisible warm-up window stays up before it is hidden (it is kept for the process)


def warm_up():
    """Start QtWebEngine (renderer process, graphics and compositing set-up) in the background, so opening the first terminal
    does not stall or make the main window blink. A view only does its graphics set-up when it is really shown, so this shows a
    one-pixel, fully transparent, click-through tool window far off-screen for a couple of seconds, then hides it. Called a
    moment after start-up, only when the embedded terminal is switched on. Idempotent; never raises."""
    global _warm
    if _warm is not None or not available():
        return
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        view = QWebEngineView()
        view.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        view.setWindowOpacity(0.0)
        view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        view.setUrl(QUrl("about:blank"))
        view.resize(64, 64)
        view.move(-32000, -32000)
        view.show()
        _warm = view
        QTimer.singleShot(WARM_SHOWN_MS, view.hide)
    except Exception:  # noqa: BLE001 - a warm-up is an optimisation only
        _warm = None
