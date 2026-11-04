from time import monotonic

from PySide6.QtCore import QObject, Signal

from .api import EnrichRequest, OverviewRequest, ResourceInfo, column_attr

RETRY_AFTER_S = 60  # a plugin whose enrich() failed is left alone for this long


def resource_info(row):
    return ResourceInfo(row.name, getattr(row, "namespace", "") or "", row.raw or {})


class PluginHost(QObject):
    """One per cluster workspace: asks the enabled plugins for the columns of the rows on screen, off the GUI thread,
    and keeps what they answer.

    Values are cached per (plugin, kind, resource); a resource is only asked about again when it is new (another uid),
    or when the plugin sets `refresh_s` and its values are older than that, or a minute after an answer that came with
    warnings. The values live on each row as
    `row.extra[attribute]`, which is where the table model reads them.
    """

    updated = Signal(str)  # the cache gained values for this kind: tables should repaint their plugin columns
    overview_updated = Signal()  # the Overview cards (or their values) changed

    def __init__(self, manager, context, run, clock=monotonic, parent=None):
        super().__init__(parent)
        self.manager, self.context, self._run, self._clock = manager, context, run, clock
        self._cache = {}  # (plugin id, kind, resource key) -> (uid, {attribute: value}, asked again at or None)
        self._busy = set()  # (plugin id, kind) with a request in flight
        self._retry_at = {}  # (plugin id, kind) -> not before this time
        self._overview_values = {}  # plugin id -> {card key: value}, the last answer (kept while the next is computed)
        self._overview_due = {}  # plugin id -> compute the cards again at this time
        self._overview_busy = False

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
                stale = entry is not None and entry[2] is not None and now >= entry[2]
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
        request = EnrichRequest(plugin.id, kind, self.context, due, self.manager.settings_for(plugin.id, self.context),
                                cache_dir=self.manager.cache_dir(plugin.id))

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
        # When to ask again: after `refresh_s` for a complete answer (never, without it); soon for one that came with
        # warnings, since something was missing (an expired login, a service that was down) and may be fixed by now.
        if request.warnings:
            ask_again = now + RETRY_AFTER_S
        else:
            ask_again = now + plugin.refresh_s if plugin.refresh_s else None
        for info in request.resources:
            values = result.get(info.key) or {}
            self._cache[(plugin.id, kind, info.key)] = (
                info.uid, {column_attr(plugin.id, k): v for k, v in values.items() if k in known}, ask_again)
        if request.warnings:  # it answered, but something was wrong on the way: use the values, show the problem
            self.manager.record_error(plugin.id, "; ".join(request.warnings))
        else:
            self.manager.clear_error(plugin.id)
        self.updated.emit(kind)

    # ---- the Overview cards ---------------------------------------------------------------------
    def overview_cards(self):
        """[(card id, title)] of the enabled plugins' cards that have a value now."""
        return [(f"{plugin.id}:{card.key}", card.title) for plugin in self.manager.overview_plugins()
                for card in plugin.overview_cards if self._overview_values.get(plugin.id, {}).get(card.key) is not None]

    def overview_values(self):
        """{card id: value} for those cards."""
        return {f"{plugin.id}:{key}": value for plugin in self.manager.overview_plugins()
                for key, value in self._overview_values.get(plugin.id, {}).items() if value is not None}

    def refresh_overview(self, list_nodes):
        """The cluster Overview was loaded: compute the plugins' cards when they are due, on a worker thread.

        `list_nodes()` (called on the worker) returns the node rows; they are listed once for all the plugins that are
        due. A plugin's cards are computed again after its `overview_refresh_s`; until then, and while that runs, the
        last values stay shown."""
        now = self._clock()
        due = [p for p in self.manager.overview_plugins() if now >= self._overview_due.get(p.id, 0)]
        if not due or self._overview_busy:
            return
        self._overview_busy = True
        settings = {p.id: self.manager.settings_for(p.id, self.context) for p in due}
        caches = {p.id: self.manager.cache_dir(p.id) for p in due}

        def work():
            nodes = [resource_info(row) for row in list_nodes()]
            answers = {}
            for plugin in due:
                request = OverviewRequest(plugin.id, self.context, nodes, settings[plugin.id], caches[plugin.id])
                try:
                    answers[plugin.id] = (plugin.overview(request), None, request.warnings)
                except Exception as error:  # one plugin's bug must not hide the others' cards
                    answers[plugin.id] = (None, str(error) or type(error).__name__, [])
            return answers

        def done(answers):
            self._overview_busy = False
            self._store_overview(due, answers)

        def failed(message):
            self._overview_busy = False
            self._store_overview(due, {p.id: (None, str(message), []) for p in due})

        self._run(work, done, on_error=failed)

    def _store_overview(self, plugins, answers):
        now = self._clock()
        for plugin in plugins:
            values, error, warnings = answers.get(plugin.id, (None, "no answer", []))
            if error is None and not isinstance(values, dict):
                error = f"overview() returned {type(values).__name__}, expected a dict"
            if error is not None:
                self._overview_due[plugin.id] = now + RETRY_AFTER_S
                self.manager.record_error(plugin.id, error)
                continue
            self._overview_values[plugin.id] = {k: v for k, v in values.items() if k in {c.key for c in plugin.overview_cards}}
            if warnings:
                self._overview_due[plugin.id] = now + RETRY_AFTER_S
                self.manager.record_error(plugin.id, "; ".join(warnings))
            else:
                self._overview_due[plugin.id] = now + (plugin.overview_refresh_s or 0)
                self.manager.clear_error(plugin.id)
        self.overview_updated.emit()

    def forget(self, plugin_id=None):
        """Drop cached values (of one plugin, or all), e.g. after its settings changed."""
        self._cache = {k: v for k, v in self._cache.items() if plugin_id is not None and k[0] != plugin_id}
        for store in (self._overview_values, self._overview_due):
            for key in [k for k in store if plugin_id is None or k == plugin_id]:
                del store[key]
