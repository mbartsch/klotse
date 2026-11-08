"""The Prometheus / VictoriaMetrics service the user chose for a cluster, kept in the settings file (`[metrics_sources]`, `ctx.N` and `ref.N`, the
text as typed: `namespace/service:port[/path prefix]`). No entry means: look for one automatically. Nothing here talks to a cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store


class MetricsSources:
    GROUP = "metrics_sources"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _all(self):
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys() if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            return {str(self.store.value(f"ctx.{n}")): str(self.store.value(f"ref.{n}", "")) for n in numbers if self.store.value(f"ctx.{n}")}
        finally:
            self.store.endGroup()

    def get(self, context):
        """The service reference typed for `context`, or "" (look for one)."""
        return self._all().get(context, "")

    def set(self, context, text):
        """Keep `text` for `context`; "" forgets it. Raises OSError when the settings file cannot be written."""
        entries = self._all()
        text = str(text).strip()
        if text:
            entries[context] = text
        else:
            entries.pop(context, None)
        self.store.remove(self.GROUP)
        for number, (name, ref) in enumerate(entries.items()):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", name)
            self.store.setValue(f"{self.GROUP}/ref.{number}", ref)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")
