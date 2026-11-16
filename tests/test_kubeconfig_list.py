"""The kubeconfig list (extra files) and the option to write cloud clusters into the main kubeconfig (off by default)."""
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog


def store(tmp_path):
    return QSettings(str(tmp_path / "nested" / "Klotse.ini"), QSettings.IniFormat)


def file_at(tmp_path, name):
    path = tmp_path / name
    path.write_text("apiVersion: v1\n", encoding="utf-8")
    return str(path)


def test_write_main_is_off_by_default(tmp_path):
    assert KubeconfigSettings(store(tmp_path)).write_main is False


def test_extra_files_are_listed_in_order_without_duplicates_and_survive_a_restart(tmp_path):
    first = KubeconfigSettings(store(tmp_path))
    a, b = file_at(tmp_path, "a.klotse"), file_at(tmp_path, "b.klotse")
    first.add_extra(a)
    first.add_extra(b)
    first.add_extra(a)
    assert KubeconfigSettings(store(tmp_path)).extra_paths() == [a, b]


def test_a_missing_extra_file_is_not_listed_and_removal_works(tmp_path):
    settings = KubeconfigSettings(store(tmp_path))
    a = file_at(tmp_path, "a.klotse")
    settings.add_extra(a)
    Path(a).unlink()
    assert KubeconfigSettings(store(tmp_path)).extra_paths() == []
    b = file_at(tmp_path, "b.klotse")
    settings.add_extra(b)
    settings.remove_extra(b)
    assert settings.extra_paths() == []


def test_adding_a_missing_file_is_refused(tmp_path):
    settings = KubeconfigSettings(store(tmp_path))
    with pytest.raises(ValueError):
        settings.add_extra(str(tmp_path / "absent"))
    assert settings.extra_paths() == []


def test_write_main_is_saved_and_shown_in_preferences(qtbot, tmp_path):
    KubeconfigSettings(store(tmp_path)).set_write_main(True)
    assert KubeconfigSettings(store(tmp_path)).write_main is True
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg")
    qtbot.addWidget(dialog)
    assert dialog.chosen_write_main() is True
    dialog.write_main_box.setChecked(False)
    assert dialog.chosen_write_main() is False


def test_preferences_checkbox_is_unticked_by_default(qtbot, tmp_path):
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg")
    qtbot.addWidget(dialog)
    assert dialog.chosen_write_main() is False
