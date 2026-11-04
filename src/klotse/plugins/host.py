from time import monotonic

from PySide6.QtCore import QObject, Signal

from .api import EnrichRequest, ResourceInfo, column_attr

RETRY_AFTER_S = 60  # a plugin whose enrich() failed is left alone for this long


def resource_info(row):
    return ResourceInfo(row.name, getattr(row, "namespace", "") or "", row.raw or {})


class PluginHost(QObject):
    """One per cluster workspace: asks the enabled plugins for the columns of the rows on screen, off the GUI thread,
    and keeps what they answer.

    Values are cached per (plugin, kind, resource); a resource is only asked about again when it is new (another uid),
    or when the plugin sets `refresh_s` and its values are older than that. The values live on each row as
    `row.extra[attribute]`, which is where the table model reads them.
    """

    updated = Signal(str)  # the cache gained values for this kind: tables should repaint their plugin columns

    def __init__(self, manager, context, run, clock=monotonic, parent=None):
        super().__init__(parent)
        self.manager, self.context, self._run, self._clock = manager, context, run, clock
        self._cache = {}  # (plugin id, kind, resource key) -> (uid, {attribute: value}, fetched at)
        self._busy = set()  # (plugin id, kind) with a request in flight
        self._retry_at = {}  # (plugin id, kind) -> not before this time

    def rows_loaded(self, kind, rows):
        """Rows of `kind` were put in a table: show what is cached and ask for what is missing or stale."""
        plugins = self.manager.enabled_for(kind)
        if not plugins:
            self.apply(kind, rows)
            return
        now = self._clock()
        for plugin in plugins:
            slot = (plugin.id, kind)
            due = []
            for row in rows:
                info = resource_info(row)
                entry = self._cache.get((plugin.id, kind, info.key))
                stale = bool(plugin.refresh_s) and entry is not None and now - entry[2] >= plugin.refresh_s
                if entry is None or entry[0] != info.uid or stale:
                    due.append(info)
            if due and slot not in self._busy and now >= self._retry_at.get(slot, 0):
                self._start(plugin, kind, due)
        self.apply(kind, rows)

    def apply(self, kind, rows):
        """Put the cached values of every enabled plugin on the rows (row.extra)."""
        plugins = self.manager.enabled_for(kind)
        for row in rows:
            key = resource_info(row).key
            values = {}
            for plugin in plugins:
                entry = self._cache.get((plugin.id, kind, key))
                if entry is not None:
                    values.update(entry[1])
            row.extra = values

    def _start(self, plugin, kind, due):
        slot = (plugin.id, kind)
        self._busy.add(slot)
        request = EnrichRequest(plugin.id, kind, self.context, due, self.manager.settings_for(plugin.id))

        def done(result):
            self._busy.discard(slot)
            self._store(plugin, kind, request, result)

        def failed(message):
            self._busy.discard(slot)
            self._retry_at[slot] = self._clock() + RETRY_AFTER_S
            self.manager.record_error(plugin.id, str(message))

        self._run(lambda: plugin.enrich(request), done, on_error=failed)

    def _store(self, plugin, kind, request, result):
        if not isinstance(result, dict):
            self._retry_at[(plugin.id, kind)] = self._clock() + RETRY_AFTER_S
            self.manager.record_error(plugin.id, f"enrich() returned {type(result).__name__}, expected a dict")
            return
        known = {col.key for col in plugin.columns}
        now = self._clock()
        for info in request.resources:
            values = result.get(info.key) or {}
            self._cache[(plugin.id, kind, info.key)] = (
                info.uid, {column_attr(plugin.id, k): v for k, v in values.items() if k in known}, now)
        self.manager.clear_error(plugin.id)
        self.updated.emit(kind)

    def forget(self, plugin_id=None):
        """Drop cached values (of one plugin, or all), e.g. after its settings changed."""
        self._cache = {k: v for k, v in self._cache.items() if plugin_id is not None and k[0] != plugin_id}
