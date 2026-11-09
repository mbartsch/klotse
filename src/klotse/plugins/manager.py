import importlib
import importlib.util
import sys
import types
import uuid
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Signal, Slot

from ..ui.settings_store import default_store
from .api import API_VERSION, Action, Column, OverviewCard, Plugin, Setting, column_attr, valid_id
from .manifest import declarations, read_manifest

ENTRY_POINT_GROUP = "klotse.plugins"
MANIFEST_ENTRY_POINT_GROUP = "klotse.plugin_manifests"
BUNDLED_MODULES = ("klotse_plugins.aws",)  # shipped inside Klotse; loaded like any other plugin, disabled until enabled


@dataclass
class Loaded:
    """One thing the manager found: a working plugin, or the reason it could not be loaded."""

    id: str  # the plugin's id; for a failure, what was being loaded
    source: str  # "bundled", "entry point" or "plugins folder"
    origin: str  # module, entry point or file
    plugin: object = None
    error: str = ""
    manifest: object = None
    loading: bool = False
    loader: object = None
    generation: int = 0


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
    if type(obj.requires_api) is not int or not 1 <= obj.requires_api <= API_VERSION:
        raise ValueError(f"it needs plugin API {obj.requires_api}, this Klotse provides {API_VERSION}")
    for group, kind in ((obj.columns, Column), (obj.actions, Action), (obj.overview_cards, OverviewCard), (obj.settings, Setting),
                        (obj.cluster_settings, Setting)):
        if not all(isinstance(item, kind) for item in group):
            raise TypeError(f"plugin {obj.id}: columns, actions, overview cards and settings must be Column, Action, OverviewCard and Setting objects")
        keys = [item.key for item in group]
        if len(keys) != len(set(keys)) or not all(keys):
            raise ValueError(f"plugin {obj.id}: column and setting keys must be non-empty and unique")
    if not all(isinstance(item.kinds, tuple) and item.kinds for item in (*obj.columns, *obj.actions)):
        raise ValueError(f"plugin {obj.id}: the kinds of a column or action must be a non-empty tuple such as ('Node',)")
    return obj


def _load_file(path):
    """Import a plugin from a .py file or a package folder in the plugins folder."""
    path = Path(path)
    name = "klotse_user_plugin_" + path.stem.replace("-", "_").replace(".", "_") + "_" + uuid.uuid4().hex
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


class _LoadSignals(QObject):
    finished = Signal(object)


class _LoadPlugin(QRunnable):
    def __init__(self, item):
        super().__init__()
        self.item, self.generation = item, item.generation
        self.signals = _LoadSignals()

    def run(self):
        plugin, error = None, ""
        try:
            plugin = _as_plugin(self.item.loader())
            if declarations(plugin) != declarations(self.item.manifest):
                raise ValueError("plugin metadata or static declarations do not match its manifest")
        except (Exception, SystemExit) as problem:
            plugin, error = None, f"{type(problem).__name__}: {problem}"
        try:
            self.signals.finished.emit((self.item, self.generation, plugin, error))
        except RuntimeError:
            pass  # manager was closed while importing


class PluginManager(QObject):
    """Finds plugins, remembers which are enabled and their settings, and tells the tables which columns to show.

    Plugins come from three places: modules bundled with Klotse (`bundled`), pip packages that declare an entry
    point in the `klotse.plugins` group, and .py files or packages in `folder`. None runs until the user enables it.
    A plugin that fails to load or to run is reported, never allowed to break Klotse.
    """

    load_finished = Signal(str, str)  # plugin id, error (empty on success)
    changed = Signal()  # plugins were (de)activated, reloaded or their settings changed
    settings_changed = Signal(str)  # a plugin's setting changed (emitted just before `changed`): its cached values are stale
    errors_changed = Signal()  # a plugin reported a problem while running (or it was cleared)

    def __init__(self, store=None, folder=None, bundled=BUNDLED_MODULES, entry_points=True, parent=None):
        super().__init__(parent)
        self.store = store if store is not None else default_store()
        self.folder = Path(folder) if folder else None
        self._bundled, self._use_entry_points = tuple(bundled), entry_points
        self.errors = {}  # plugin id -> the last problem while running it
        self.loaded = []
        self._loads = {}
        self._enabled = self._read_enabled()
        self.discover()

    # ---- finding plugins ---------------------------------------------------
    @staticmethod
    def _bundled_manifest(module):
        # Searching files directly avoids importing a package __init__ just to locate its data.
        relative = Path(*module.split("."))
        for root in sys.path:
            base = Path(root or ".") / relative
            for path in (base.with_suffix(".json"), base / "klotse-plugin.json"):
                if path.is_file():
                    return path
        raise ValueError(f"required plugin manifest is missing for {module}")

    @staticmethod
    def _entry_manifest(entry):
        distribution = entry.dist
        companions = [e for e in distribution.entry_points
                      if e.group == MANIFEST_ENTRY_POINT_GROUP and e.name == entry.name]
        if len(companions) != 1:
            raise ValueError("required companion klotse.plugin_manifests entry point is missing or ambiguous")
        # Entry point values must use Python object-reference syntax for packaging tools.
        # This is a resource stem only: never call load() or import its package.
        parts = companions[0].value.split(".")
        if not parts or not all(part.isidentifier() for part in parts):
            raise ValueError("manifest entry point must be a dotted resource stem such as my_plugin.klotse_manifest")
        relative = Path(*parts).with_suffix(".json")
        return distribution.locate_file(relative)

    def _candidates(self):
        for module in self._bundled:
            yield "bundled", module, lambda m=module: self._bundled_manifest(m), lambda m=module: importlib.import_module(m)
        if self._use_entry_points:
            for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
                yield "entry point", f"{entry.name} = {entry.value}", lambda e=entry: self._entry_manifest(e), entry.load
        if self.folder is not None and self.folder.is_dir():
            for path in sorted(self.folder.iterdir()):
                if path.name.startswith(("_", ".")):
                    continue
                if (path.suffix == ".py" and path.is_file()) or (path.is_dir() and (path / "__init__.py").is_file()):
                    manifest = path / "klotse-plugin.json" if path.is_dir() else path.with_suffix(".json")
                    yield "plugins folder", str(path), lambda p=manifest: p, lambda p=path: _load_file(p)

    def discover(self):
        found, ids = [], set()
        for source, origin, manifest_path, load in self._candidates():
            try:
                manifest = read_manifest(manifest_path())
                if manifest.id in ids:
                    raise ValueError(f"another plugin already uses the id {manifest.id!r}")
            except (Exception, SystemExit) as error:
                found.append(Loaded(origin, source, origin, error=f"{type(error).__name__}: {error}"))
                continue
            ids.add(manifest.id)
            found.append(Loaded(manifest.id, source, origin, manifest=manifest, loader=load))
        self.loaded = found
        # A startup/reload catalog pass never runs plugin code itself.
        QTimer.singleShot(0, self._load_saved)

    def _load_saved(self):
        for item in self.loaded:
            if item.manifest is not None and item.id in self._enabled and item.plugin is None and not item.loading and not item.error:
                self._start_load(item)

    def reload(self):
        self.discover()
        self.changed.emit()

    def get(self, plugin_id):
        return next((item for item in self.loaded if item.id == plugin_id and
                     (item.manifest is not None or item.plugin is not None)), None)

    def is_loading(self, plugin_id):
        item = self.get(plugin_id)
        return bool(item and item.loading)

    def _start_load(self, item):
        item.error = ""
        item.loading = True
        item.generation += 1
        worker = _LoadPlugin(item)
        self._loads[(id(item), item.generation)] = worker
        worker.signals.finished.connect(self._loaded)
        self.changed.emit()
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _loaded(self, result):
        item, generation, plugin, error = result
        self._loads.pop((id(item), generation), None)
        if not any(current is item for current in self.loaded) or generation != item.generation:
            return  # disabled or catalog replaced during import
        item.loading = False
        if not error:
            try:
                self._persist_enabled(item.id, True)
            except OSError as problem:
                error = str(problem)
        if error:
            # Preserve a saved startup preference; failed code is never active.
            item.error = error
        else:
            item.plugin = plugin
        self.changed.emit()
        self.load_finished.emit(item.id, error)

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
        item = self.get(plugin_id)
        return bool(item and item.plugin is not None and plugin_id in self._enabled)

    def set_enabled(self, plugin_id, enabled):
        """Load newly enabled code on a worker; persist only after successful validation."""
        item = self.get(plugin_id)
        if enabled and item is None:
            raise ValueError("plugin is unavailable; a valid manifest is required")
        if enabled and item.plugin is None:
            if not item.loading:
                self._start_load(item)
            return
        self._persist_enabled(plugin_id, enabled)
        if item and not enabled:
            item.generation += 1
            item.loading = False
        self.changed.emit()

    def _persist_enabled(self, plugin_id, enabled):
        wanted = [p for p in self._enabled if p != plugin_id] + ([plugin_id] if enabled else [])
        self.store.beginGroup("plugins")
        try:
            previous = {key: self.store.value(key) for key in self.store.allKeys()}
        finally:
            self.store.endGroup()
        self.store.remove("plugins")
        for number, name in enumerate(wanted):
            self.store.setValue(f"plugins/enabled.{number}", name)
        try:
            self._sync()
        except OSError:
            self.store.remove("plugins")
            for key, value in previous.items():
                self.store.setValue(f"plugins/{key}", value)
            raise
        self._enabled = wanted

    def settings_for(self, plugin_id, context=None):
        """The plugin's settings as {key: value}, saved values over the defaults. With `context`, the cluster settings
        the user set for that cluster are added (every cluster setting is present, "" when not set)."""
        item = self.get(plugin_id)
        values = {}
        for setting in (item.manifest or item.plugin).settings if item else ():
            saved = self.store.value(f"plugin_{plugin_id}/{setting.key}", None)
            if saved is None:
                values[setting.key] = setting.default
            elif setting.kind == "bool":
                values[setting.key] = str(saved).lower() in ("true", "1", "yes")
            else:
                values[setting.key] = str(saved)
        if context is not None and item:
            mine = self.cluster_settings_for(plugin_id, context)
            values.update({s.key: mine.get(s.key, "") for s in (item.manifest or item.plugin).cluster_settings})
        return values

    def set_setting(self, plugin_id, key, value):
        self.store.setValue(f"plugin_{plugin_id}/{key}", value)
        self._sync()
        self.settings_changed.emit(plugin_id)
        self.changed.emit()

    # ---- settings per cluster ----------------------------------------------
    def cluster_plugins(self):
        """The enabled plugins that have settings to set for each cluster."""
        return [plugin for plugin in self.active() if plugin.cluster_settings]

    def _read_clusters(self, plugin_id):
        """{context: {key: value}} saved for the plugin: numbered keys `context.N` and `<key>.N` in [plugin_<id>_clusters]."""
        self.store.beginGroup(f"plugin_{plugin_id}_clusters")
        try:
            raw = {key: self.store.value(key) for key in self.store.childKeys()}
        finally:
            self.store.endGroup()

        def text(value):  # QSettings returns a list for a value containing a comma
            return ",".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value if value is not None else "")

        found = {}
        for key, value in raw.items():
            head, _, number = key.partition(".")
            if head == "context" and number.isdigit() and text(value):
                found[text(value)] = {k.partition(".")[0]: text(v) for k, v in raw.items()
                                      if k.partition(".")[2] == number and k.partition(".")[0] != "context" and text(v)}
        return found

    def cluster_settings_for(self, plugin_id, context):
        """What the user set for this cluster: {key: value} for the cluster settings that are not empty."""
        item = self.get(plugin_id)
        known = {s.key for s in (item.manifest or item.plugin).cluster_settings} if item else set()
        return {k: v for k, v in self._read_clusters(plugin_id).get(context, {}).items() if k in known}

    def set_cluster_settings(self, plugin_id, context, values):
        """Save the plugin's settings for one cluster (empty values are forgotten); raises OSError if it cannot be saved."""
        every = self._read_clusters(plugin_id)
        mine = {k: str(v).strip() for k, v in values.items() if str(v).strip()}
        if mine:
            every[context] = mine
        else:
            every.pop(context, None)
        self.store.remove(f"plugin_{plugin_id}_clusters")
        for number, (name, items) in enumerate(sorted(every.items())):
            self.store.setValue(f"plugin_{plugin_id}_clusters/context.{number}", name)
            for key, value in items.items():
                self.store.setValue(f"plugin_{plugin_id}_clusters/{key}.{number}", value)
        self._sync()
        self.settings_changed.emit(plugin_id)
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

    def overview_plugins(self):
        """The enabled plugins that add cards to the cluster Overview."""
        return [plugin for plugin in self.active() if plugin.overview_cards]

    def actions_for(self, kind, multi=False):
        """The enabled plugins' actions for `kind` as [(plugin, [actions])]; with several rows selected only the
        actions that allow it (`multi`)."""
        found = []
        for plugin in self.active():
            actions = [a for a in plugin.actions if kind in a.kinds and (a.multi or not multi)]
            if actions:
                found.append((plugin, actions))
        return found

    def cache_dir(self, plugin_id):
        """A folder the plugin may keep downloaded data in (next to the settings file). Not created here."""
        return Path(self.store.fileName()).parent / "plugin-cache" / plugin_id

    def record_error(self, plugin_id, message):
        self.errors[plugin_id] = message
        self.errors_changed.emit()

    def clear_error(self, plugin_id):
        if self.errors.pop(plugin_id, None) is not None:
            self.errors_changed.emit()
