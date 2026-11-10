"""Colour themes: two built in (Classic, Harbour) and any number loaded from files in the `themes` folder.

A theme is a handful of colours. `apply` turns the active one into the application palette and style sheet, and
`use` also publishes it as the module constants (`BG`, `MUTED`, `ACCENT`, ...) that painted and HTML views read **when
they draw**, so switching themes needs no restart. Code that needs a colour must read `theme.MUTED` at use time, never
copy it with `from .theme import MUTED`. Labels that should be muted call `muted(label)` (a palette role), not a
hard-coded style sheet.

Theme files are INI (see `template_text`): a `[theme]` name and a `[colors]` section of `#rrggbb` values. Values are
checked against a strict pattern because they end up in a style sheet.
"""
import configparser
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen

REQUIRED = ("bg", "base", "border", "text", "muted", "accent")
OPTIONAL = ("accent_hi", "selected_row", "hover", "input_border", "warn", "warn_bg", "ok", "bad")  # left out: accent, accent, border, border/muted mix, amber, dark amber
COLOR_KEYS = REQUIRED + OPTIONAL
DESCRIPTIONS = {
    "bg": "window, toolbars, tab bar, menus",
    "base": "lists, tables, text boxes",
    "border": "dividers, borders and buttons",
    "text": "normal text",
    "muted": "secondary text and headings",
    "accent": "selection and filled controls (white text sits on it)",
    "accent_hi": "thin marks: active tab underline, focus ring",
    "selected_row": "selected table row",
    "hover": "hovered buttons and scroll bars",
    "ok": "healthy states: Running, Ready, Bound",
    "bad": "failing states: CrashLoopBackOff, Failed, NotReady",
    "warn": "text of warnings (banners, hints, highlighted rules)",
    "warn_bg": "background of warning banners",
    "input_border": "edge of text boxes and drop-downs (keep it 3:1 against base)",
}
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
THEME_SUFFIX = ".ini"


class ThemeError(ValueError):
    """A theme file that cannot be used; the message says what to fix."""


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    base: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hi: str
    selected_row: str
    hover: str
    input_border: str
    warn: str = "#f5a524"
    warn_bg: str = "#3a2c0f"
    ok: str = "#4cc38a"
    bad: str = "#ff7b72"
    source: str = ""  # the file it was loaded from; empty for the built-in themes


def _mix(a, b, weight=0.5):
    """Two #rrggbb colours blended; `weight` is how much of `b` (0.5 = halfway)."""
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - weight) + int(b[i:i + 2], 16) * weight):02x}" for i in (1, 3, 5))


CLASSIC = Theme("Classic", bg="#1e2127", base="#16181d", border="#2b2f38", text="#d7dae0", muted="#9aa3b2",
                accent="#3474d7", accent_hi="#3d7eff", selected_row="#2b5099", hover="#394050", input_border=_mix("#2b2f38", "#9aa3b2"))
# Neutrals tinted toward the logo's deep teal-navy, one teal accent from the uniform. Amber (#f5a524) is deliberately
# not an accent: the app uses it for warnings (banners, hints, states).
HARBOUR = Theme("Harbour", bg="#16232b", base="#101b22", border="#293b43", text="#e3eceb", muted="#91a5a8",
                accent="#197f7d", accent_hi="#35b5b0", selected_row="#145450", hover="#263b42", input_border="#5b7279")
# The light theme: paper-white lists on a cool grey window, the same teal accent (white text on it stays readable) and
# a dark amber for warnings (the dark theme's bright amber would be unreadable on white).
DAYLIGHT = Theme("Daylight", bg="#eef2f3", base="#ffffff", border="#d3dadd", text="#1d2a30", muted="#4f636b",
                 accent="#197f7d", accent_hi="#197f7d", selected_row="#6aacaa", hover="#c2ccd0",
                 input_border="#7d8c93", warn="#8a4b00", warn_bg="#fdf0d3", ok="#17794a", bad="#c4302b")
BUILTIN = (HARBOUR, CLASSIC, DAYLIGHT)
DEFAULT = HARBOUR


def _color(key, value):
    value = value.strip()
    if not HEX.fullmatch(value):
        raise ThemeError(f"'{key}' must be a colour like #1b8582 (got '{value}')")
    value = value.lower()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


def parse_theme(text, source="", default_name="Theme"):
    """Read a theme from INI text. Raises ThemeError with a readable message."""
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ThemeError(f"not a valid theme file: {str(error).splitlines()[0]}") from None
    name = parser.get("theme", "name", fallback="").strip() or default_name
    if not parser.has_section("colors"):
        raise ThemeError("missing the [colors] section")
    given = {}
    for key, value in parser.items("colors"):
        if key not in COLOR_KEYS:
            raise ThemeError(f"unknown colour '{key}' (known: {', '.join(COLOR_KEYS)})")
        given[key] = _color(key, value)
    missing = [key for key in REQUIRED if key not in given]
    if missing:
        raise ThemeError("missing colour(s): " + ", ".join(missing))
    given.setdefault("accent_hi", given["accent"])
    given.setdefault("selected_row", given["accent"])
    given.setdefault("hover", given["border"])
    given.setdefault("warn", "#f5a524")
    given.setdefault("warn_bg", "#3a2c0f")
    given.setdefault("ok", "#4cc38a")
    given.setdefault("bad", "#ff7b72")
    given.setdefault("input_border", _mix(given["border"], given["muted"], 0.75))  # visible against the list colour
    return Theme(name=name, source=str(source), **given)


def load_file(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise ThemeError(f"cannot read the file: {error}") from None
    return parse_theme(text, source=path, default_name=path.stem)


def template_text(theme):
    """A theme file with every colour and a comment each: what 'Export current theme' writes, ready to edit."""
    lines = ["; Klotse theme. Save it in the themes folder (Preferences > General > Open themes folder).",
             "; Colours are #rrggbb or #rgb. accent_hi, selected_row and hover are optional.",
             "[theme]", f"name = {theme.name} copy", "", "[colors]"]
    for key in COLOR_KEYS:
        lines.append(f"; {DESCRIPTIONS[key]}")
        lines.append(f"{key} = {getattr(theme, key)}")
    return "\n".join(lines) + "\n"


def _close_icon(color):
    """A small X as a PNG file for the style sheet (QSS can only take images from files); cached by colour."""
    folder = Path(tempfile.gettempdir()) / "klotse-icons"
    path = folder / f"close-{color.lstrip('#')}.png"
    if not path.is_file():
        image = QImage(28, 28, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(8, 8, 20, 20)
        painter.drawLine(20, 8, 8, 20)
        painter.end()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            image.save(str(path))
        except OSError:
            return None
    return path.as_posix() if path.is_file() else None


def stylesheet(t):
    quiet, loud = _close_icon(t.muted), _close_icon(t.text)
    close = (f"QTabBar::close-button {{ image: url('{quiet}'); subcontrol-position: right; margin: 0 0 0 6px; "
             f"padding: 2px; border-radius: 4px; }}\n"
             f"QTabBar::close-button:hover {{ image: url('{loud}'); background: {t.hover}; }}") if quiet and loud else ""
    return _sheet(t) + close + "\n"


def _sheet(t):
    rail = _mix(t.bg, t.base, 0.45)
    return f"""
QMainWindow {{ background: {t.base}; }}
QDialog {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {t.bg},stop:1 {t.base}); }}
QLabel#resourcePageTitle {{ font-size: 23px; font-weight: 600; padding: 0; }}
QLabel#pageEyebrow {{ font-size: 10px; }}
QTableView#resourceGrid {{ background: transparent; border: 0; }}
QTableView#resourceGrid::item {{ background: {t.bg}; }}
QTableView#resourceGrid::item:selected {{ background: {t.selected_row}; }}
QTableView#resourceGrid::item:hover:!selected {{ background: {t.hover}; }}
QPushButton#primaryAction {{ background: {t.accent}; color: white; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 4px; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton:focus {{ border-color: {t.accent_hi}; }}
QToolButton:pressed {{ background: {t.selected_row}; }}
QMenuBar {{ background: {t.bg}; color: {t.muted}; border-bottom: 1px solid {t.border}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 9px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {t.hover}; color: {t.text}; }}
QToolBar {{ background: {t.bg}; border: 0; border-bottom: 1px solid {t.border}; spacing: 7px; padding: 5px 8px; }}
QToolBar QToolButton {{ padding: 5px 10px; border: 1px solid transparent; border-radius: 6px; }}
QToolBar QToolButton:focus {{ border-color: {t.accent_hi}; }}
QToolBar QToolButton:hover {{ background: {t.hover}; }}
QStatusBar {{ background: {t.bg}; color: {t.muted}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: 0; }}
QDockWidget {{ titlebar-close-icon: none; border: 0; }}
QDockWidget::title {{ background: {t.bg}; color: {t.muted}; padding: 8px 10px; border-bottom: 1px solid {t.border}; }}
QDockWidget#clustersDock {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {t.bg},stop:1 {t.base}); border-right: 1px solid {t.border}; }}
QDockWidget#detailsDock {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {t.bg},stop:1 {t.base}); border-left: 1px solid {t.border}; }}
QWidget#navigatorPanel, QWidget#navigatorBrand {{ background: {rail}; }}
QWidget#detailPanel {{ background: {rail}; }}
QDockWidget#detailsDock::title {{ background: {rail}; }}
QWidget#detailIdentity {{ background: transparent; border-bottom: 1px solid {t.border}; }}
QWidget#detailViewBar {{ background: {t.base}; border: 1px solid {t.border}; border-radius: 7px; padding: 2px; }}
QPlainTextEdit#detailYamlEditor, QTextBrowser#detailFormatted, QTextBrowser#detailEvents, QTextBrowser#detailRelated {{ background: transparent; border: 0; }}
QPlainTextEdit#detailYamlEditor:focus, QTextBrowser#detailFormatted:focus, QTextBrowser#detailEvents:focus, QTextBrowser#detailRelated:focus {{ border: 1px solid {t.accent_hi}; }}
QPushButton#detailViewButton {{ background: transparent; border: 0; border-radius: 5px; padding: 5px 9px; color: {t.muted}; }}
QPushButton#detailViewButton:hover {{ background: {t.hover}; color: {t.text}; border: 0; }}
QPushButton#detailViewButton:checked {{ background: {t.selected_row}; color: {t.text}; border: 0; }}
QPushButton#detailViewButton:focus {{ border: 1px solid {t.accent_hi}; }}
QTreeView {{ background: {t.base}; border: 0; }}
QTreeView::item {{ padding: 4px 3px; }}
QTreeView:focus::item:selected, QTableView:focus::item:selected {{ border: 2px solid {t.accent_hi}; }}
QTreeView::item:hover:!selected {{ background: {t.hover}; }}
QTreeView::item:selected {{ background: {t.accent}; color: white; }}
QTreeView#navigator::item:selected, QTreeView#navigator::item:hover, QTreeView#navigator::item {{ background: transparent; }}
QTreeView#navigator::item:selected {{ border: 0; }}
QTreeView#navigator {{ background: transparent; selection-background-color: transparent; }}
QTreeView#navigator:focus {{ border: 0; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {t.border}; }}
QTabBar::tab {{ background: {t.bg}; color: {t.muted}; padding: 8px 14px; border: 0; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent_hi}; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabWidget#workspaceTabs QTabBar::tab {{ padding: 4px 10px; font-size: 11px; }}
QLabel#scopeBreadcrumb {{ padding: 5px 10px; color: {t.muted}; }}
QSplitter::handle {{ background: {t.border}; }}
QSplitter::handle:hover {{ background: {t.accent_hi}; }}
QHeaderView::section {{ background: {t.bg}; color: {t.muted}; border: 0; border-bottom: 1px solid {t.border};
                        padding: 7px 9px; font-weight: 600; }}
QTableView {{ background: {t.base}; alternate-background-color: {t.bg}; gridline-color: transparent; border: 0; selection-background-color: {t.selected_row}; selection-color: {t.text}; }}
QTableView::item {{ padding: 4px 7px; border-bottom: 1px solid {t.border}; }}
QTableView::item:hover:!selected {{ background: {t.hover}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit, QPlainTextEdit {{ background: {t.base}; border: 1px solid {t.input_border}; border-radius: 6px; padding: 5px 8px; selection-background-color: {t.accent}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus, QPlainTextEdit:focus {{ border-color: {t.accent_hi}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t.base}; border: 1px solid {t.border}; selection-background-color: {t.selected_row}; padding: 4px; }}
QPlainTextEdit {{ border-radius: 0; }}
QPlainTextEdit[readOnly="true"], QTextBrowser {{ border: 0; }}
QPlainTextEdit[readOnly="true"]:focus, QTextBrowser:focus {{ border: 1px solid {t.accent_hi}; }}
QPushButton {{ background: {t.border}; border: 1px solid transparent; border-radius: 6px; padding: 6px 12px; }}
QPushButton:focus {{ border-color: {t.accent_hi}; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.input_border}; }}
QPushButton:pressed {{ background: {t.accent}; color: white; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.bg}; border-color: {t.border}; }}
QPushButton:checked {{ background: {t.accent}; color: white; }}
QPushButton:default {{ background: {t.accent}; color: white; }}
QPushButton:default:disabled {{ background: {t.bg}; color: {t.muted}; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 10px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {t.muted}; }}
QToolTip {{ background: {t.bg}; color: {t.text}; border: 1px solid {t.input_border}; padding: 5px 7px; }}
QMenu {{ background: {t.bg}; border: 1px solid {t.border}; padding: 5px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.accent}; color: white; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 7px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {t.border}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {t.hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def use(t):
    """Make `t` the active theme: set the module constants views read when they draw."""
    global current, BG, BASE, BORDER, TEXT, MUTED, ACCENT, ACCENT_HI, SELECTED_ROW, HOVER, WARN, WARN_BG, OK, BAD
    current = t
    BG, BASE, BORDER, TEXT, MUTED = t.bg, t.base, t.border, t.text, t.muted
    ACCENT, ACCENT_HI, SELECTED_ROW, HOVER = t.accent, t.accent_hi, t.selected_row, t.hover
    WARN, WARN_BG, OK, BAD = t.warn, t.warn_bg, t.ok, t.bad


use(DEFAULT)


def warning_style():
    """Style sheet for a warning banner in the active theme (read when the banner is built)."""
    return f"background:{WARN_BG}; color:{WARN}; padding:6px; border-radius:4px;"


def muted(widget):
    """Draw a label in the muted colour through the palette, so it follows a theme change at once."""
    widget.setForegroundRole(QPalette.PlaceholderText)


def apply(app, t=None):
    app.setStyle("Fusion")
    t = t or current
    use(t)
    pal = QPalette()
    for role, color in ((QPalette.Window, t.bg), (QPalette.WindowText, t.text), (QPalette.Base, t.base),
                        (QPalette.AlternateBase, t.bg), (QPalette.Text, t.text), (QPalette.Button, t.border),
                        (QPalette.ButtonText, t.text), (QPalette.ToolTipBase, t.bg), (QPalette.ToolTipText, t.text),
                        (QPalette.Highlight, t.accent), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.PlaceholderText, t.muted)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))


class ThemeSettings(QObject):
    """Which theme is chosen (`appearance/theme` in the settings store) and which themes exist.

    Themes are the built-in ones plus the `.ini` files in `folder`. A file that cannot be used, or that reuses another
    theme's name, is left out and reported in `problems()`. A saved name that no longer exists falls back to the default.
    """

    def __init__(self, store, folder=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.folder = Path(folder) if folder else Path(store.fileName()).parent / "themes"
        self.saved = str(store.value("appearance/theme", "") or "")

    def scan(self):
        """(themes by name, problems as (file, message)); built-in themes first, then files in name order."""
        themes = {t.name: t for t in BUILTIN}
        problems = []
        files = sorted(self.folder.glob("*" + THEME_SUFFIX)) if self.folder.is_dir() else []
        for path in files:
            try:
                t = load_file(path)
            except ThemeError as error:
                problems.append((path.name, str(error)))
                continue
            if t.name in themes:
                problems.append((path.name, f"the name '{t.name}' is already used"))
                continue
            themes[t.name] = t
        return themes, problems

    def themes(self):
        return self.scan()[0]

    def problems(self):
        return self.scan()[1]

    def selected(self):
        return self.themes().get(self.saved, DEFAULT)

    def set(self, name):
        if name not in self.themes():
            raise ValueError(f"Unknown theme: {name}")
        self.store.setValue("appearance/theme", name)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")
        self.saved = name
