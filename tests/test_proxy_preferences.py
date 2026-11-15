"""Preferences > General shows the app proxy; Save validates it and stores it, Cancel leaves it as it was."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.proxy_settings import ProxySettings
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


def test_the_proxy_row_saves_a_valid_address_and_bypass_list(qtbot, tmp_path, monkeypatch):
    proxy = ProxySettings(store(tmp_path))
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg", proxy=proxy)
    qtbot.addWidget(dialog)
    dialog.proxy_url.setText("http://proxy.corp:3128")
    dialog.no_proxy_edit.setText("localhost, 10.0.0.0/8")
    dialog._save()
    saved = ProxySettings(store(tmp_path))
    assert saved.url == "http://proxy.corp:3128" and saved.no_proxy == ["localhost", "10.0.0.0/8"]


def test_an_unusable_proxy_is_reported_and_nothing_is_saved(qtbot, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warned.append(args[2]))
    proxy = ProxySettings(store(tmp_path))
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg", proxy=proxy)
    qtbot.addWidget(dialog)
    dialog.proxy_url.setText("proxy.corp:3128")
    dialog._save()
    assert warned and "http://" in warned[0]
    assert ProxySettings(store(tmp_path)).url is None


def test_without_a_proxy_object_there_is_no_proxy_row(qtbot, tmp_path):
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg")
    qtbot.addWidget(dialog)
    assert not hasattr(dialog, "proxy_url")
