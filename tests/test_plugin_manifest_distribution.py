"""Discover an installed plugin from real distribution metadata without importing it."""
import json
import sys
from importlib import metadata

from PySide6.QtCore import QSettings

from klotse.plugins import manager as plugin_manager


def test_distribution_manifest_is_read_without_importing_the_package(tmp_path, monkeypatch):
    package = tmp_path / "external_plugin"
    package.mkdir()
    marker = tmp_path / "imported"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('imported')\n",
        encoding="utf-8",
    )
    (package / "klotse_manifest.json").write_text(json.dumps({
        "manifest_version": 1, "id": "external", "requires_api": 1,
    }), encoding="utf-8")
    info = tmp_path / "external_plugin-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: external-plugin\nVersion: 1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[klotse.plugins]\nexternal = external_plugin:plugin\n"
        "[klotse.plugin_manifests]\nexternal = external_plugin.klotse_manifest\n",
        encoding="utf-8",
    )
    distribution = next(metadata.distributions(path=[str(tmp_path)]))
    entries = [entry for entry in distribution.entry_points if entry.group == "klotse.plugins"]
    monkeypatch.setattr(plugin_manager.metadata, "entry_points", lambda **kwargs: entries)
    monkeypatch.syspath_prepend(str(tmp_path))
    manager = plugin_manager.PluginManager(
        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat), bundled=(), entry_points=True,
    )
    found = manager.get("external")
    assert found is not None and found.manifest.id == "external"
    assert found.plugin is None and not found.error
    assert not marker.exists()
    assert "external_plugin" not in sys.modules
