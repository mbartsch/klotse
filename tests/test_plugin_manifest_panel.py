import json
import sys
import threading

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QFormLayout

from klotse.plugins.manager import PluginManager
from klotse.ui.plugins_panel import PluginsPanel


def plugin_panel(qtbot, tmp_path, body):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "sample.py").write_text(body, encoding="utf-8")
    (folder / "sample.json").write_text(json.dumps({
        "manifest_version": 1, "id": "sample", "name": "Sample", "description": "Declared before import",
        "requires_api": 1, "overview_cards": [{"key": "cost", "title": "Cost"}],
        "settings": [{"key": "region", "label": "Region", "default": "eu"}],
    }), encoding="utf-8")
    manager = PluginManager(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
                            folder=folder, bundled=(), entry_points=False)
    panel = PluginsPanel(manager)
    qtbot.addWidget(panel)
    return manager, panel


def test_manifest_metadata_and_saved_settings_are_visible_without_import(qtbot, tmp_path):
    manager, panel = plugin_panel(qtbot, tmp_path, "raise AssertionError('must not import')")
    assert manager.loaded[0].plugin is None
    assert panel.title.text() == "Sample"
    assert panel.description.text() == "Declared before import"
    assert "Overview cards: Cost" in panel.provides.text()
    region = panel.settings_form.itemAt(0, QFormLayout.FieldRole).widget()
    assert region.text() == "eu"
    region.setText("us")
    region.editingFinished.emit()
    assert manager.settings_for("sample")["region"] == "us"
    assert manager.loaded[0].plugin is None


def test_loading_keeps_preferences_responsive_and_failure_reverts_checkbox(qtbot, tmp_path, monkeypatch):
    gate = threading.Event()
    monkeypatch.setitem(sys.modules, "_klotse_panel_test_gate", gate)
    manager, panel = plugin_panel(qtbot, tmp_path,
        "import sys\nsys.modules['_klotse_panel_test_gate'].wait(5)\nraise SystemExit('cannot start')\n")
    try:
        panel.list.item(0).setCheckState(Qt.Checked)
        assert manager.loaded[0].loading
        assert "loading" in panel.list.item(0).text().lower()
        assert "Loading plugin" in panel.error.text()
        assert not manager.is_enabled("sample")
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not manager.loaded[0].loading)
    assert panel.list.item(0).checkState() == Qt.Unchecked
    assert "cannot start" in panel.error.text()
    assert "loading" not in panel.list.item(0).text().lower()
