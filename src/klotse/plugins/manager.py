import importlib
import importlib.util
import sys
import types
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal

from ..ui.settings_store import default_store
from .api import Column, Plugin, Setting, column_attr, valid_id

ENTRY_POINT_GROUP = "klotse.plugins"
BUNDLED_MODULES = ()  # modules shipped inside Klotse; loaded like any other plugin and just as disabled until enabled


@dataclass
class Loaded:
    """One thing the manager found: a working plugin, or the reason it could not be loaded."""

    id: str  # the plugin's id; for a failure, what was being loaded
    source: str  # "bundled", "entry point" or "plugins folder"
    origin: str  # module, entry point or file
    plugin: object = None
    error: str = ""


def _as_plugin(obj):
    """Turn what a module, entry point or file provides into a validated Plugin (raises with a clear message)."""
    if isinstance(obj, types.ModuleType):
        module = obj
        obj = getattr(module, "plugin", None) or getattr(module, "register", None)
        if obj is None:
            raise ValueError("the module has neither a `plugin` object nor a `register()` function")
    if isinstance(obj, type) and issubclass(obj, Plugin):
        obj = obj()
    elif not isinstance(obj, Plugin) and callable(obj):
        obj = obj()
    if not isinstance(obj, Plugin):
        raise TypeError("it does not provide a klotse.plugins.Plugin")
    if not valid_id(obj.id):
        raise ValueError(f"invalid plugin id {obj.id!r} (use lower case letters, digits, '-' and '_')")
    for group, kind in ((obj.columns, Column), (obj.settings, Setting)):
        if not all(isinstance(item, kind) for item in group):
            raise TypeError(f"plugin {obj.id}: columns and settings must be Column and Setting objects")
        keys = [item.key for item in group]
        if len(keys) != len(set(keys)) or not all(keys):
            raise ValueError(f"plugin {obj.id}: column and setting keys must be non-empty and unique")
    if not all(isinstance(col.kinds, tuple) and col.kinds for col in obj.columns):
        raise ValueError(f"plugin {obj.id}: a column's kinds must be a non-empty tuple such as ('Node',)")
    return obj


def _load_file(path):
    """Import a plugin from a .py file or a package folder in the plugins folder."""
    path = Path(path)
    name = "klotse_user_plugin_" + path.stem.replace("-", "_").replace(".", "_")
    target = path / "__init__.py" if path.is_dir() else path
    spec = importlib.util.spec_from_file_location(name, target, submodule_search_locations=[str(path)] if path.is_dir() else None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and relative imports need the module to be registered while it runs
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class PluginManager(QObject):
    """Finds plugins, remembers which are enabled and their settings, and tells the tables which columns to show.

    Plugins come from three places: modules bundled with Klotse (`bundled`), pip packages that declare an entry
    point in the `klotse.plugins` group, and .py files or packages in `folder`. None runs until the user enables it.
    A plugin that fails to load or to run is reported, never allowed to break Klotse.
    """

    changed = Signal()  # plugins were (de)activated, reloaded or their settings changed
    errors_changed = Signal()  # a plugin reported a problem while running (or it was cleared)

    def __init__(self, store=None, folder=None, bundled=BUNDLED_MODULES, entry_points=True, parent=None):
        super().__init__(parent)
        self.store = store if store is not None else default_store()
        self.folder = Path(folder) if folder else None
        self._bundled, self._use_entry_points = tuple(bundled), entry_points
        self.errors = {}  # plugin id -> the last problem while running it
        self.loaded = []
        self._enabled = self._read_enabled()
        self.discover()

    # ---- finding plugins ---------------------------------------------------
    def _candidates(self):
        for module in self._bundled:
            yield "bundled", module, lambda m=module: importlib.import_module(m)
        if self._use_entry_points:
            for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
                yield "entry point", f"{entry.name} = {entry.value}", entry.load
        if self.folder is not None and self.folder.is_dir():
            for path in sorted(self.folder.iterdir()):
                if path.name.startswith(("_", ".")):
                    continue
                if (path.suffix == ".py" and path.is_file()) or (path.is_dir() and (path / "__init__.py").is_file()):
                    yield "plugins folder", str(path), lambda p=path: _load_file(p)

    def discover(self):
        found, ids = [], set()
        for source, origin, load in self._candidates():
            try:
                plugin = _as_plugin(load())
                if plugin.id in ids:
                    raise ValueError(f"another plugin already uses the id {plugin.id!r}")
            except Exception as error:  # a broken plugin must never stop Klotse (or the other plugins)
                found.append(Loaded(origin, source, origin, None, f"{type(error).__name__}: {error}"))
                continue
            ids.add(plugin.id)
            found.append(Loaded(plugin.id, source, origin, plugin))
        self.loaded = found

    def reload(self):
        self.discover()
        self.changed.emit()

    def get(self, plugin_id):
        return next((item for item in self.loaded if item.plugin is not None and item.id == plugin_id), None)

    # ---- enabled / settings ------------------------------------------------
    def _read_enabled(self):
        self.store.beginGroup("plugins")
        try:
            found = {}
            for key in self.store.childKeys():
                head, _, number = key.partition(".")
                if head == "enabled" and number.isdigit():
                    found[int(number)] = str(self.store.value(key) or "")
        finally:
            self.store.endGroup()
        return [found[n] for n in sorted(found) if found[n]]

    def is_enabled(self, plugin_id):
        return plugin_id in self._enabled

    def set_enabled(self, plugin_id, enabled):
        """Enable or disable a plugin and save it; raises OSError (and changes nothing) if it cannot be saved."""
        wanted = [p for p in self._enabled if p != plugin_id] + ([plugin_id] if enabled else [])
        self.store.remove("plugins")
        for number, name in enumerate(wanted):
            self.store.setValue(f"plugins/enabled.{number}", name)
        self._sync()
        self._enabled = wanted
        self.changed.emit()

    def settings_for(self, plugin_id):
        """The plugin's settings as {key: value}, saved values over the defaults."""
        item = self.get(plugin_id)
        values = {}
        for setting in item.plugin.settings if item else ():
            saved = self.store.value(f"plugin_{plugin_id}/{setting.key}", None)
            if saved is None:
                values[setting.key] = setting.default
            elif setting.kind == "bool":
                values[setting.key] = str(saved).lower() in ("true", "1", "yes")
            else:
                values[setting.key] = str(saved)
        return values

    def set_setting(self, plugin_id, key, value):
        self.store.setValue(f"plugin_{plugin_id}/{key}", value)
        self._sync()
        self.changed.emit()

    def _sync(self):
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the plugin settings to {self.store.fileName()}")

    # ---- what the rest of Klotse asks ----------------------------------------
    def active(self):
        """The enabled plugins that loaded fine."""
        return [item.plugin for item in self.loaded if item.plugin is not None and item.id in self._enabled]

    def enabled_for(self, kind):
        return [plugin for plugin in self.active() if any(kind in col.kinds for col in plugin.columns)]

    def columns_for(self, kind):
        """Table column tuples (heading, attribute[, formatter]) the enabled plugins add to `kind` tables."""
        columns = []
        for plugin in self.active():
            for col in plugin.columns:
                if kind in col.kinds:
                    attr = column_attr(plugin.id, col.key)
                    columns.append((col.title, attr, col.format) if col.format else (col.title, attr))
        return columns

    def record_error(self, plugin_id, message):
        self.errors[plugin_id] = message
        self.errors_changed.emit()

    def clear_error(self, plugin_id):
        if self.errors.pop(plugin_id, None) is not None:
            self.errors_changed.emit()
