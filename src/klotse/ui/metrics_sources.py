"""The Prometheus / VictoriaMetrics service the user chose for a cluster, kept in the settings file (`[metrics_sources]`, `ctx.N` and `ref.N`, the
text as typed: `namespace/service:port[/path prefix]`, plus `via.N` = `forward` and `local.N` when it is read through a port-forward instead of the
API server proxy). No entry means: look for one automatically. Nothing here talks to a cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store


class MetricsSources:
    GROUP = "metrics_sources"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _entries(self):
        """{context: (text, via, local)}; via is "proxy" or "forward", local the local port asked for (0 = a free one)."""
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys() if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            found = {}
            for n in numbers:
                name = self.store.value(f"ctx.{n}")
                if not name:
                    continue
                via = "forward" if str(self.store.value(f"via.{n}", "")) == "forward" else "proxy"
                local = str(self.store.value(f"local.{n}", "0"))
                found[str(name)] = (str(self.store.value(f"ref.{n}", "")), via, int(local) if local.isdigit() and int(local) < 65536 else 0)
            return found
        finally:
            self.store.endGroup()

    def get(self, context):
        """The service reference typed for `context`, or "" (look for one)."""
        return self._entries().get(context, ("", "proxy", 0))[0]

    def get_via(self, context):
        """"forward" when `context`'s service is reached through a port-forward, else "proxy"."""
        return self._entries().get(context, ("", "proxy", 0))[1]

    def get_local(self, context):
        """The local port asked for the forward (0 = a free one)."""
        return self._entries().get(context, ("", "proxy", 0))[2]

    def set(self, context, text, via="proxy", local=0):
        """Keep `text` for `context`; "" forgets it. Raises OSError when the settings file cannot be written."""
        entries = self._entries()
        text = str(text).strip()
        if text:
            entries[context] = (text, "forward" if via == "forward" else "proxy", int(local or 0))
        else:
            entries.pop(context, None)
        self.store.remove(self.GROUP)
        for number, (name, (ref, how, port)) in enumerate(entries.items()):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", name)
            self.store.setValue(f"{self.GROUP}/ref.{number}", ref)
            if how == "forward":
                self.store.setValue(f"{self.GROUP}/via.{number}", "forward")
                self.store.setValue(f"{self.GROUP}/local.{number}", port)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")
