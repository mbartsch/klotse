"""Races and persistence around asynchronous plugin activation."""
import threading

from PySide6.QtCore import QSettings

from klotse.plugins import Plugin
from klotse.plugins.manager import Loaded, PluginManager


def manager_with_loader(tmp_path, loader):
    manager = PluginManager(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
                            bundled=(), entry_points=False)
    manifest = Plugin()
    manifest.id = "slow"
    item = Loaded("slow", "plugins folder", "slow.py", manifest=manifest, loader=loader)
    manager.loaded = [item]
    return manager, item


def slow_loader(gate, finished):
    def load():
        gate.wait(5)
        plugin = Plugin()
        plugin.id = "slow"
        finished.set()
        return plugin
    return load


def test_disabling_during_import_cannot_reactivate_plugin(qtbot, tmp_path):
    gate, finished = threading.Event(), threading.Event()
    manager, item = manager_with_loader(tmp_path, slow_loader(gate, finished))
    manager.set_enabled("slow", True)
    manager.set_enabled("slow", False)
    gate.set()
    qtbot.waitUntil(lambda: finished.is_set() and not manager._loads)
    assert not item.loading and item.plugin is None
    assert not manager.is_enabled("slow")
    assert manager.store.value("plugins/enabled.0") is None


def test_reloading_catalog_ignores_old_import_completion(qtbot, tmp_path):
    gate, finished = threading.Event(), threading.Event()
    manager, item = manager_with_loader(tmp_path, slow_loader(gate, finished))
    manager.set_enabled("slow", True)
    manager.reload()
    assert manager.loaded == []
    gate.set()
    qtbot.waitUntil(lambda: finished.is_set() and not manager._loads)
    assert not manager.is_enabled("slow")
    assert manager.store.value("plugins/enabled.0") is None


def test_async_save_failure_does_not_activate_or_leave_unsaved_enablement(qtbot, tmp_path):
    def load():
        plugin = Plugin()
        plugin.id = "slow"
        return plugin
    manager, item = manager_with_loader(tmp_path, load)
    manager.store.setValue("plugins/enabled.0", "unavailable")
    manager._enabled = ["unavailable"]
    manager._sync = lambda: (_ for _ in ()).throw(OSError("read-only"))
    manager.set_enabled("slow", True)
    qtbot.waitUntil(lambda: not item.loading)
    assert item.error == "read-only" and item.plugin is None
    assert not manager.is_enabled("slow")
    assert manager._enabled == ["unavailable"]
    assert manager.store.value("plugins/enabled.0") == "unavailable"
    assert manager.store.value("plugins/enabled.1") is None


def test_import_runs_on_worker_and_persistence_runs_on_manager_thread(qtbot, tmp_path):
    owner = threading.get_ident()
    calls = []
    def load():
        calls.append(("import", threading.get_ident()))
        plugin = Plugin()
        plugin.id = "slow"
        return plugin
    manager, item = manager_with_loader(tmp_path, load)
    sync = manager._sync
    def persist():
        calls.append(("persist", threading.get_ident()))
        sync()
    manager._sync = persist
    manager.set_enabled("slow", True)
    qtbot.waitUntil(lambda: not item.loading)
    assert calls[0][0] == "import" and calls[0][1] != owner
    assert calls[1] == ("persist", owner)
    assert manager.is_enabled("slow")
