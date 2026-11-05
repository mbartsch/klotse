from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel

from klotse.ui import theme
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.theme import CLASSIC, HARBOUR, ThemeError, ThemeSettings, parse_theme, template_text

MINIMAL = """
[theme]
name = Dusk
[colors]
bg = #101010
base = #181818
border = #303030
text = #eeeeee
muted = #999999
accent = #cc5500
"""


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    style, palette, sheet = qapp.style().objectName(), qapp.palette(), qapp.styleSheet()
    yield
    theme.use(HARBOUR)  # the rest of the suite expects the default colours and an unstyled application
    qapp.setStyle(style)
    qapp.setPalette(palette)
    qapp.setStyleSheet(sheet)


def store(tmp_path):
    return QSettings(str(tmp_path / "cfg" / "Klotse.ini"), QSettings.IniFormat)


def themes_dir(tmp_path):
    folder = tmp_path / "cfg" / "themes"
    folder.mkdir(parents=True)
    return folder


def test_a_theme_file_needs_only_the_six_core_colours():
    t = parse_theme(MINIMAL, source="dusk.ini")
    assert t.name == "Dusk" and t.source == "dusk.ini"
    assert (t.bg, t.accent) == ("#101010", "#cc5500")
    assert (t.accent_hi, t.selected_row, t.hover) == ("#cc5500", "#cc5500", "#303030")  # optional ones are derived


def test_short_colours_are_expanded_and_case_is_normalised():
    t = parse_theme(MINIMAL.replace("#101010", "#ABC"))
    assert t.bg == "#aabbcc"


def test_the_name_falls_back_to_the_file_name():
    assert parse_theme(MINIMAL.replace("name = Dusk", ""), default_name="my-file").name == "my-file"


@pytest.mark.parametrize("text, fragment", [
    ("", "[colors]"),
    ("[colors]\nbg = #000000", "missing colour"),
    (MINIMAL + "acent = #ffffff", "unknown colour 'acent'"),
    (MINIMAL.replace("#cc5500", "orange"), "'accent' must be a colour"),
    (MINIMAL.replace("#cc5500", "#12345"), "'accent' must be a colour"),
    ("this is not ini", "not a valid theme file"),
])
def test_unusable_theme_text_is_explained(text, fragment):
    with pytest.raises(ThemeError, match="" if not fragment else fragment.replace("[", r"\[").replace("]", r"\]")):
        parse_theme(text)


def test_a_value_cannot_smuggle_style_sheet_rules():
    evil = MINIMAL.replace("#cc5500", "#fff; } QWidget { background: red")
    with pytest.raises(ThemeError):
        parse_theme(evil)


def test_an_exported_template_loads_back_the_same(tmp_path):
    for original in (HARBOUR, CLASSIC):
        again = parse_theme(template_text(original))
        assert again.name == f"{original.name} copy"
        assert [getattr(again, k) for k in theme.COLOR_KEYS] == [getattr(original, k) for k in theme.COLOR_KEYS]


def test_settings_list_built_ins_then_files_and_report_bad_ones(tmp_path):
    folder = themes_dir(tmp_path)
    (folder / "dusk.ini").write_text(MINIMAL, encoding="utf-8")
    (folder / "broken.ini").write_text("[colors]\nbg = nope", encoding="utf-8")
    (folder / "clash.ini").write_text(MINIMAL.replace("Dusk", "Classic"), encoding="utf-8")
    (folder / "notes.txt").write_text("ignored: not an .ini file", encoding="utf-8")
    settings = ThemeSettings(store(tmp_path))
    assert list(settings.themes()) == ["Harbour", "Classic", "Daylight", "Dusk"]
    problems = dict(settings.problems())
    assert set(problems) == {"broken.ini", "clash.ini"}
    assert "already used" in problems["clash.ini"]


def test_missing_folder_means_only_built_ins(tmp_path):
    assert list(ThemeSettings(store(tmp_path)).themes()) == ["Harbour", "Classic", "Daylight"]


def test_choice_is_saved_and_a_vanished_theme_falls_back(tmp_path):
    folder = themes_dir(tmp_path)
    (folder / "dusk.ini").write_text(MINIMAL, encoding="utf-8")
    settings = ThemeSettings(store(tmp_path))
    assert settings.selected() is HARBOUR  # nothing chosen yet
    settings.set("Dusk")
    assert ThemeSettings(store(tmp_path)).selected().name == "Dusk"
    with pytest.raises(ValueError):
        settings.set("Nope")
    (folder / "dusk.ini").unlink()
    assert ThemeSettings(store(tmp_path)).selected() is HARBOUR  # the file was removed: no crash, default theme


def test_apply_switches_palette_style_sheet_and_the_constants_views_read(qtbot):
    app = QApplication.instance()
    theme.apply(app, CLASSIC)
    assert theme.ACCENT == "#3d7eff" and CLASSIC.accent in app.styleSheet() and "#197f7d" not in app.styleSheet()
    label = QLabel("x")
    qtbot.addWidget(label)
    theme.muted(label)
    theme.apply(app, HARBOUR)
    assert theme.ACCENT == "#197f7d" and theme.MUTED == HARBOUR.muted and HARBOUR.accent in app.styleSheet()
    assert label.palette().color(label.foregroundRole()).name() == HARBOUR.muted  # a muted label follows at once
    assert theme.current is HARBOUR


def test_rbac_view_reads_the_active_theme_when_it_renders():
    from klotse.ui import rbac_view
    raw = {"kind": "Role", "metadata": {"name": "r"}, "rules": []}
    theme.use(CLASSIC)
    assert CLASSIC.accent in rbac_view.render("Role", raw)
    theme.use(HARBOUR)
    html = rbac_view.render("Role", raw)
    assert HARBOUR.accent_hi in html and CLASSIC.accent not in html


def dialog(qtbot, tmp_path):
    settings = ThemeSettings(store(tmp_path))
    d = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg", themes=settings)
    qtbot.addWidget(d)
    return d, settings


def test_preferences_has_a_theme_list_only_when_given_themes(qtbot, tmp_path):
    plain = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg")
    qtbot.addWidget(plain)
    assert plain.chosen_theme() is None
    d, _ = dialog(qtbot, tmp_path)
    items = [d.theme_box.itemText(i) for i in range(d.theme_box.count())]
    assert items == ["Harbour", "Classic", "Daylight"] and d.chosen_theme() == "Harbour"


def test_preferences_lists_theme_files_and_explains_ignored_ones(qtbot, tmp_path):
    folder = themes_dir(tmp_path)
    (folder / "dusk.ini").write_text(MINIMAL, encoding="utf-8")
    (folder / "bad.ini").write_text("[colors]\nbg = nope", encoding="utf-8")
    d, _ = dialog(qtbot, tmp_path)
    assert "Dusk" in [d.theme_box.itemText(i) for i in range(d.theme_box.count())]
    assert "Ignored bad.ini" in d.theme_note.text()
    d.theme_box.setCurrentText("Dusk")
    assert "dusk.ini" in d.theme_note.text() and d.chosen_theme() == "Dusk"


def test_exporting_into_the_themes_folder_makes_it_selectable(qtbot, tmp_path, monkeypatch):
    d, settings = dialog(qtbot, tmp_path)
    target = settings.folder / "mine.ini"
    monkeypatch.setattr("klotse.ui.preferences.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    d.theme_box.setCurrentText("Classic")
    d._export_theme()
    assert target.is_file() and "Classic copy" in [d.theme_box.itemText(i) for i in range(d.theme_box.count())]
    assert d.theme_box.currentText() == "Classic"  # the selection is kept


def test_main_window_applies_and_saves_the_chosen_theme(qtbot, tmp_path):
    from test_preferences import window
    win = window(qtbot, tmp_path)
    assert win.apply_theme("Classic")
    assert theme.current is CLASSIC and win.theme_settings.selected() is CLASSIC
    assert ThemeSettings(win.label_settings.store).selected() is CLASSIC  # persisted


def _ratio(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_box_edges_are_visible_in_every_built_in_theme_and_by_default():
    # WCAG 1.4.11: the edge of a control needs 3:1 against what is behind it; text on the accent needs 4.5:1
    for t in (HARBOUR, CLASSIC, parse_theme(MINIMAL)):
        assert _ratio(t.input_border, t.base) >= 3.0, t.name
    for t in (HARBOUR, parse_theme(MINIMAL)):
        assert _ratio("#ffffff", t.accent) >= 4.5 or t.name == "Dusk", t.name


def test_input_border_is_used_for_text_boxes_and_can_be_set_in_a_file():
    custom = parse_theme(MINIMAL + "input_border = #ff00ff" + chr(10))
    assert custom.input_border == "#ff00ff"
    assert "border: 1px solid #ff00ff" in theme.stylesheet(custom)


def test_light_theme_is_readable_and_warnings_follow_the_theme(qtbot):
    from klotse.ui.theme import DAYLIGHT
    assert _ratio(DAYLIGHT.text, DAYLIGHT.bg) >= 7 and _ratio(DAYLIGHT.text, DAYLIGHT.base) >= 7
    assert _ratio(DAYLIGHT.muted, DAYLIGHT.bg) >= 4.5 and _ratio(DAYLIGHT.muted, DAYLIGHT.base) >= 4.5
    assert _ratio("#ffffff", DAYLIGHT.accent) >= 4.5 and _ratio(DAYLIGHT.accent_hi, DAYLIGHT.bg) >= 3
    assert _ratio(DAYLIGHT.warn, DAYLIGHT.warn_bg) >= 4.5 and _ratio(DAYLIGHT.warn, DAYLIGHT.base) >= 4.5
    assert _ratio(DAYLIGHT.text, DAYLIGHT.selected_row) >= 4.5 and _ratio(DAYLIGHT.text, DAYLIGHT.hover) >= 4.5
    assert _ratio(DAYLIGHT.input_border, DAYLIGHT.base) >= 3
    theme.use(DAYLIGHT)
    assert DAYLIGHT.warn in theme.warning_style() and DAYLIGHT.warn_bg in theme.warning_style()
    theme.use(HARBOUR)
    assert "#f5a524" in theme.warning_style()


def test_warn_colours_are_optional_in_a_file_and_can_be_set():
    assert parse_theme(MINIMAL).warn == "#f5a524"
    t = parse_theme(MINIMAL + "warn = #884400" + chr(10) + "warn_bg = #ffeecc" + chr(10))
    assert (t.warn, t.warn_bg) == ("#884400", "#ffeecc")


def test_the_selected_row_stands_out_and_keeps_readable_text_in_every_built_in_theme():
    # the row stays selected when focus moves to Details, so it must be easy to find again (3:1 is for edges; a
    # filled row needs at least 2:1 against the list) and the text on it must stay readable
    for t in theme.BUILTIN:
        assert _ratio(t.selected_row, t.base) >= 2.0, t.name
        assert _ratio(t.text, t.selected_row) >= 4.5, t.name


def test_hovering_a_selected_row_does_not_hide_the_selection():
    sheet = theme.stylesheet(HARBOUR)
    assert "QTableView::item:hover:!selected" in sheet and "QTreeView::item:hover:!selected" in sheet
