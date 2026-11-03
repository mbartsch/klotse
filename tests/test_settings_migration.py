from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from klotse.ui import settings_store
from klotse.ui.customizations import LabelSettings
from klotse.ui.preferences import KubeconfigSettings
from klotse.ui.terminal_settings import TerminalSettings


@pytest.fixture
def redirected(tmp_path):
    """Send Qt's per-user settings location to tmp_path so the real preferences are never read or written."""
    original = Path(QSettings(QSettings.IniFormat, QSettings.UserScope, "probe", "probe").fileName()).parent.parent
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    try:
        yield tmp_path
    finally:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(original))


def test_old_kubeman_settings_are_copied_once_to_the_new_location(redirected):
    old = QSettings(QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman")
    old.setValue("columns.Pod/hidden", "age")
    old.setValue("terminal/macos", "iTerm2")
    old.sync()
    old_file = Path(old.fileName())
    new_file = Path(QSettings(QSettings.IniFormat, QSettings.UserScope, "Klotse", "Klotse").fileName())
    assert old_file != new_file and not new_file.exists()

    labels = LabelSettings()  # no injected store: this is the path real users take
    assert Path(labels.store.fileName()) == new_file
    assert labels.layout("Pod")["hidden"] == ["age"]
    assert TerminalSettings().mac_terminal == "iTerm2"
    assert old_file.exists()  # the old file stays, so going back to an older version loses nothing

    labels.set_layout("Pod", [], [])  # changes land only in the new file
    assert "hidden=" in new_file.read_text() and "age" in old_file.read_text()
    later = settings_store.default_store()  # a second start must not overwrite newer settings with the old ones
    assert later.value("columns.Pod/hidden") == ""


def test_fresh_install_without_old_settings_starts_with_defaults(redirected):
    labels = LabelSettings()
    assert labels.labels("Node") == ["karpenter.sh/nodepool"] and KubeconfigSettings().path is None
    assert not Path(QSettings(QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman").fileName()).exists()


def test_failed_copy_falls_back_to_defaults(redirected, monkeypatch):
    old = QSettings(QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman")
    old.setValue("terminal/macos", "iTerm2")
    old.sync()

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(settings_store.shutil, "copy2", broken)
    assert TerminalSettings().mac_terminal == "Terminal"
