"""Preferences > Metrics query: whether the query window suggests metric names while you type (autocomplete). Off by default: it asks the source
for every metric name it has once per window, which can be a very large list on a big Prometheus or VictoriaMetrics. Nothing here talks to a
cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store


class QueryDefaults:
    KEY = "metrics_query/autocomplete"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    @property
    def autocomplete(self):
        return str(self.store.value(self.KEY, "false")).lower() in ("true", "1")

    def set(self, autocomplete):
        self.store.setValue(self.KEY, bool(autocomplete))
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")
