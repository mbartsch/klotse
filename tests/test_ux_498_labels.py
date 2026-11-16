"""#498: Preferences labels name their field (buddy), so a screen reader and a click on the label reach the field."""
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.theme import ThemeSettings


def test_kubeconfig_and_theme_labels_are_tied_to_their_fields(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "Klotse.ini"), QSettings.IniFormat)
    dialog = PreferencesDialog(KubeconfigSettings(settings), Path(tmp_path / "cfg"), themes=ThemeSettings(settings))
    qtbot.addWidget(dialog)
    labels = {label.text(): label for label in dialog.findChildren(QLabel) if label.text()}
    assert labels["Kubeconfig"].buddy() is dialog.path
    assert labels["Theme"].buddy() is dialog.theme_box
