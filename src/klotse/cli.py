"""Command-line entry points handled before the GUI starts. No Qt is imported at module level.

    klotse --version                      print the version and exit
    klotse --self-test [REPORT_FILE]      check that this installation works (used to verify packaged builds)
    klotse --terminal-session APP JSON    run one macOS shell session (used by the packaged app, see ui/terminal.py)
    klotse --install-desktop-entry        Linux, for pip/pipx installs: add Klotse to the application menu
    klotse --uninstall-desktop-entry      Linux: remove that menu entry again

Anything else is left to the GUI. A packaged Windows app has no console, so --self-test can write its report to a
file; --version prints to stdout as usual.
"""
import os
import shutil
import sys

from . import __display_version__ as __version__  # what the app reports; plain X.Y.Z for a tagged release


def _self_test_checks():
    """Yield (name, callable) pairs; each callable returns a short detail string or raises."""
    def frozen():
        return "packaged app" if getattr(sys, "frozen", False) else "Python install (pip, pipx or a source checkout)"

    def imports():
        import kubernetes
        import yaml

        from .ui import main_window  # noqa: F401
        return f"kubernetes {kubernetes.__version__}, PyYAML {yaml.__version__}"

    def qt():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window: this only proves Qt and its plugins load
        from PySide6.QtCore import qVersion
        from PySide6.QtGui import QImageReader
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        formats = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        if "png" not in formats:
            raise RuntimeError("the Qt PNG image plugin is missing")
        return f"Qt {qVersion()}, platform '{app.platformName()}', image formats {', '.join(sorted(formats)[:6])}, ..."

    def logo():
        from .ui import branding
        pixmap = branding.square_logo(64)
        path = branding.logo_path()
        if path is None or pixmap.isNull():
            raise RuntimeError("the logo file is missing from the installation")
        return f"{path.name} ({pixmap.width()}x{pixmap.height()})"

    def dialog():
        from .ui.about import AboutDialog
        dialog = AboutDialog()
        title = dialog.windowTitle()
        dialog.deleteLater()
        return title

    def plugins():
        # the plugins bundled with Klotse must load (a missing hidden import in a packaged app would show here)
        import tempfile

        from PySide6.QtCore import QSettings
        from .plugins.manager import BUNDLED_MODULES, PluginManager
        scratch = QSettings(os.path.join(tempfile.gettempdir(), "klotse-selftest.ini"), QSettings.IniFormat)
        manager = PluginManager(scratch, folder=None, bundled=BUNDLED_MODULES, entry_points=False)
        failed = [f"{found.origin}: {found.error}" for found in manager.loaded if found.plugin is None]
        if failed:
            raise RuntimeError("; ".join(failed))
        return f"{len(manager.loaded)} bundled plugin(s) load"

    def embedded_terminal():
        # The embedded terminal needs QtWebEngine, the bundled xterm.js files and, on Windows, pywinpty. A packaged app must have
        # them. A pip install has the Python packages, but on Linux QtWebEngine also needs system libraries (NSS and others) that
        # a bare machine may lack: there the app uses the external terminal, so the check only reports why.
        from .ui import embedded_terminal as et
        if et.available():
            return "QtWebEngine and xterm.js are available"
        reason = et.unavailable_reason()
        if getattr(sys, "frozen", False):
            raise RuntimeError(f"the embedded terminal is missing from the package: {reason}")
        return f"not available here, the external terminal is used ({reason})"

    def kubectl():
        found = shutil.which("kubectl")
        return found or "not found on PATH (needed for shells, attach and drain)"

    return [("version", lambda: __version__), ("mode", frozen), ("imports", imports), ("qt", qt), ("logo", logo),
            ("about dialog", dialog), ("plugins", plugins), ("embedded terminal", embedded_terminal),
            ("kubectl", kubectl)]


def self_test(report_file=None):
    """Run the checks; returns 0 when every required one passed. kubectl is informational only."""
    lines, ok = [f"Klotse {__version__} self-test"], True
    for name, check in _self_test_checks():
        try:
            lines.append(f"ok    {name}: {check()}")
        except Exception as error:  # report every failure, not just the first
            ok = False
            lines.append(f"FAIL  {name}: {type(error).__name__}: {error}")
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    report = "\n".join(lines) + "\n"
    if report_file:
        with open(report_file, "w", encoding="utf-8") as handle:
            handle.write(report)
    elif sys.stdout:  # a packaged Windows app has no console: sys.stdout is None
        sys.stdout.write(report.encode("ascii", "replace").decode("ascii"))  # never fail on a legacy console encoding
    return 0 if ok else 1


def handle(argv):
    """Run a command-line action and return its exit status, or None when the GUI should start."""
    if not argv:
        return None
    if argv[0] in ("--version", "-V"):
        print(f"Klotse {__version__}")
        return 0
    if argv[0] == "--self-test":
        return self_test(argv[1] if len(argv) > 1 else None)
    if argv[0] in ("--install-desktop-entry", "--uninstall-desktop-entry"):
        from . import desktop_entry
        try:
            if argv[0] == "--install-desktop-entry":
                print("Installed:")
                for path in desktop_entry.install():
                    print(f"  {path}")
            else:
                removed = desktop_entry.uninstall()
                print("Removed:" if removed else "Nothing to remove.")
                for path in removed:
                    print(f"  {path}")
        except (RuntimeError, OSError) as error:
            print(f"klotse: {error}", file=sys.stderr)
            return 1
        return 0
    if argv[0] == "--terminal-session":
        if len(argv) < 3:
            print("usage: klotse --terminal-session APP COMMAND_JSON [TARGET...]", file=sys.stderr)
            return 2
        from .ui import terminal_session
        return terminal_session.main(argv[1:])
    return None
