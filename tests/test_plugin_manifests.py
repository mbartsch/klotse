import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.plugins.manager import PluginManager


def manifest(plugin_id="sample", **extra):
    return dict(manifest_version=1, id=plugin_id, requires_api=1, **extra)


def local(tmp_path, code=None, data=None, package=False):
    folder = tmp_path / "plugins"
    folder.mkdir(exist_ok=True)
    if package:
        target = folder / "sample"
        target.mkdir()
        source, metadata = target / "__init__.py", target / "klotse-plugin.json"
    else:
        source, metadata = folder / "sample.py", folder / "sample.json"
    source.write_text(code or "from klotse.plugins import Plugin\nclass Sample(Plugin):\n    id = 'sample'\nplugin = Sample()\n", encoding="utf-8")
    if data is not False:
        metadata.write_text(json.dumps(data or manifest()), encoding="utf-8")
    return folder, source, metadata


def manager(tmp_path, folder, **kwargs):
    return PluginManager(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat), folder=folder,
                         bundled=(), entry_points=False, **kwargs)


@pytest.mark.parametrize("package", [False, True])
def test_discovery_does_not_execute_local_code(tmp_path, package):
    marker = tmp_path / "executed"
    folder, _, _ = local(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit('stop')", package=package)
    found = manager(tmp_path, folder)
    assert found.get("sample").manifest.id == "sample"
    assert found.get("sample").plugin is None
    assert not marker.exists()


def test_enable_is_async_and_persisted_only_after_success(qtbot, tmp_path):
    marker = tmp_path / "thread"
    folder, _, _ = local(tmp_path, "import threading, time\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(str(threading.get_ident()))\ntime.sleep(.15)\n"
        "from klotse.plugins import Plugin\nclass Sample(Plugin):\n    id = 'sample'\nplugin = Sample()\n")
    found = manager(tmp_path, folder)
    found.set_enabled("sample", True)
    assert found.get("sample").loading and not found.is_enabled("sample")
    assert found.store.value("plugins/enabled.0") is None
    qtbot.waitUntil(lambda: found.is_enabled("sample"))
    assert int(marker.read_text()) != threading.get_ident()
    assert found.store.value("plugins/enabled.0") == "sample"
    found.set_enabled("sample", False)
    assert not found.active()
    found.set_enabled("sample", True)
    again = manager(tmp_path, folder)
    qtbot.waitUntil(lambda: again.is_enabled("sample"))
    assert again.active()[0].id == "sample"


@pytest.mark.parametrize("code,reason", [
    ("raise RuntimeError('broken')", "broken"),
    ("raise SystemExit('stop')", "SystemExit"),
    ("from klotse.plugins import Plugin\nclass P(Plugin):\n    id='different'\nplugin=P()", "match"),
    ("from klotse.plugins import Plugin, Column\nclass P(Plugin):\n    id='sample'\n    columns=(Column('x','X'),)\nplugin=P()", "match"),
])
def test_enable_failure_stays_disabled(qtbot, tmp_path, code, reason):
    folder, _, _ = local(tmp_path, code)
    found = manager(tmp_path, folder)
    found.set_enabled("sample", True)
    qtbot.waitUntil(lambda: not found.get("sample").loading)
    assert reason in found.get("sample").error
    assert not found.is_enabled("sample") and not found.active()
    assert found.store.value("plugins/enabled.0") is None


def test_legacy_plugin_never_runs_and_saved_state_survives(qtbot, tmp_path):
    folder, source, metadata = local(tmp_path, "raise SystemExit('never')", data=False)
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    store.setValue("plugins/enabled.0", "sample")
    store.setValue("plugin_sample/region", "saved")
    store.sync()
    found = manager(tmp_path, folder)
    assert found.loaded[0].error and "manifest" in found.loaded[0].error.lower()
    assert not found.active()
    assert found.store.value("plugins/enabled.0") == "sample"
    assert found.store.value("plugin_sample/region") == "saved"
    source.write_text("from klotse.plugins import Plugin\nclass P(Plugin):\n    id='sample'\nplugin=P()")
    metadata.write_text(json.dumps(manifest()))
    found.reload()
    qtbot.waitUntil(lambda: found.is_enabled("sample"))


@pytest.mark.parametrize("data", [dict(manifest_version=99, id="sample", requires_api=1),
    manifest(columns=[dict(key="x", title="X", kinds=[])]),
    manifest(actions=[dict(key="x", title="X", dangerous="false")]),
    manifest(settings=[dict(key="x", label="X", kind="unknown")]),
    dict(manifest_version=1, id="Bad ID", requires_api=1)])
def test_invalid_manifests_are_unavailable_without_execution(tmp_path, data):
    folder, _, _ = local(tmp_path, "raise SystemExit('never')", data=data)
    found = manager(tmp_path, folder)
    assert found.loaded[0].error and found.loaded[0].plugin is None


def test_installed_entry_point_manifest_read_without_load(tmp_path, monkeypatch):
    import klotse.plugins.manager as module
    package = tmp_path / "hostile_metadata"
    package.mkdir()
    (package / "__init__.py").write_text("raise SystemExit('metadata package imported')")
    metadata = package / "manifest.json"
    metadata.write_text(json.dumps(manifest()))
    calls = []
    distribution = SimpleNamespace(locate_file=lambda path: tmp_path / path)
    entry = SimpleNamespace(name="sample", value="hostile:plugin", dist=distribution,
                            load=lambda: calls.append("executed"))
    companion = SimpleNamespace(name="sample", value="hostile_metadata.manifest", dist=distribution,
                                group="klotse.plugin_manifests", load=lambda: calls.append("companion executed"))
    distribution.entry_points = [companion]
    monkeypatch.setattr(module.metadata, "entry_points", lambda *, group:
                        [entry] if group == "klotse.plugins" else [companion])
    found = PluginManager(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat), bundled=())
    assert found.get("sample").manifest.id == "sample" and calls == []

def test_bundled_discovery_does_not_import_package_parents(tmp_path, monkeypatch):
    parent = tmp_path / "hostile_bundle"
    parent.mkdir()
    (parent / "__init__.py").write_text("raise SystemExit('parent executed')")
    (parent / "child.py").write_text("raise SystemExit('plugin executed')")
    (parent / "child.json").write_text(json.dumps(manifest()))
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys
    found = PluginManager(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat),
                          bundled=("hostile_bundle.child",), entry_points=False)
    assert found.get("sample").manifest.id == "sample"
    assert "hostile_bundle" not in sys.modules and "hostile_bundle.child" not in sys.modules


def test_installed_legacy_entry_point_is_not_loaded(tmp_path, monkeypatch):
    import klotse.plugins.manager as module
    calls = []
    entry = SimpleNamespace(name="legacy", value="hostile:plugin",
                            dist=SimpleNamespace(entry_points=[]), load=lambda: calls.append(True))
    monkeypatch.setattr(module.metadata, "entry_points", lambda **kw: [entry])
    found = PluginManager(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat), bundled=())
    assert "companion" in found.loaded[0].error and not calls


def test_duplicate_manifest_ids_are_unavailable(tmp_path):
    folder, _, _ = local(tmp_path)
    (folder / "other.py").write_text("raise SystemExit('never')")
    (folder / "other.json").write_text(json.dumps(manifest()))
    found = manager(tmp_path, folder)
    assert len([item for item in found.loaded if item.manifest]) == 1
    assert "already uses" in next(item.error for item in found.loaded if item.error)


@pytest.mark.parametrize("resource", ["my_plugin/manifest.json", "my_plugin:manifest", "../manifest",
                                      "my-plugin.manifest", ".manifest", "plugin.", "plugin..manifest", "plugin manifest"])
def test_invalid_manifest_entry_point_is_rejected_without_execution(tmp_path, monkeypatch, resource):
    import klotse.plugins.manager as module
    calls = []
    companion = SimpleNamespace(name="sample", value=resource, group="klotse.plugin_manifests",
                                load=lambda: calls.append("companion executed"))
    distribution = SimpleNamespace(entry_points=[companion],
                                   locate_file=lambda path: calls.append("resolved"))
    entry = SimpleNamespace(name="sample", value="hostile:plugin", dist=distribution,
                            load=lambda: calls.append("executed"))
    monkeypatch.setattr(module.metadata, "entry_points", lambda **kw: [entry])
    found = PluginManager(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat), bundled=())
    assert "dotted resource stem" in found.loaded[0].error
    assert found.loaded[0].plugin is None and calls == []
