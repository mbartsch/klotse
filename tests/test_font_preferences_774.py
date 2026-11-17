from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase

from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.theme import FontSettings


def store(tmp_path):
    return QSettings(str(tmp_path / "Klotse.ini"), QSettings.IniFormat)


def test_application_font_settings_persist_family_and_size(qtbot, tmp_path):
    settings = FontSettings(store(tmp_path))
    font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    font.setPointSize(13)

    settings.set(font)
    restored = FontSettings(store(tmp_path)).selected()

    assert restored.family() == font.family()
    assert restored.pointSize() == 13


def test_preferences_exposes_application_font_controls(qtbot, tmp_path):
    fonts = FontSettings(store(tmp_path))
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "config", fonts=fonts)
    qtbot.addWidget(dialog)

    dialog.font_size_box.setValue(14)
    chosen = dialog.chosen_font()

    assert dialog.font_family_box.currentText() == fonts.selected().family()
    assert chosen.family() == fonts.selected().family()
    assert chosen.pointSize() == 14
