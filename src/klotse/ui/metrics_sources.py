"""The Prometheus / VictoriaMetrics service the user chose for a cluster, kept in the settings file (`[metrics_sources]`, numbered `ctx.N`, `ref.N`:
the text as typed, `namespace/service:port[/path prefix]`). For a port-forward: `via.N` = `forward`, `local.N` the local port asked for, and
`tls.N` / `insecure.N` = `on` / `off` for a per-cluster override (absent = the Preferences default). No entry means: look for one automatically.
`ForwardDefaults` holds the Preferences values (`[metrics_forward]`). Nothing here talks to a cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store


def _on_off(value):
    """"on" / "off" for a saved override; "" (use the default) for anything else."""
    text = str(value or "").strip().lower()
    return text if text in ("on", "off") else ""


class ForwardDefaults:
    """Preferences > Usage history through a port-forward: use SSL (https) and ignore certificate errors. Off by default."""

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _flag(self, key):
        return str(self.store.value(key, "false")).lower() in ("true", "1")

    @property
    def tls(self):
        return self._flag("metrics_forward/tls")

    @property
    def insecure(self):
        return self._flag("metrics_forward/insecure")

    def set(self, tls, insecure):
        self.store.setValue("metrics_forward/tls", bool(tls))
        self.store.setValue("metrics_forward/insecure", bool(insecure))
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")


class MetricsSources:
    GROUP = "metrics_sources"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _entries(self):
        """{context: {ref, via, local, tls, insecure}}; via is "proxy" or "forward"; tls and insecure are "on", "off" or "" (the default)."""
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys() if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            found = {}
            for n in numbers:
                name = self.store.value(f"ctx.{n}")
                if not name:
                    continue
                local = str(self.store.value(f"local.{n}", "0"))
                found[str(name)] = {
                    "ref": str(self.store.value(f"ref.{n}", "")),
                    "via": "forward" if str(self.store.value(f"via.{n}", "")) == "forward" else "proxy",
                    "local": int(local) if local.isdigit() and int(local) < 65536 else 0,
                    "tls": _on_off(self.store.value(f"tls.{n}", "")),
                    "insecure": _on_off(self.store.value(f"insecure.{n}", "")),
                }
            return found
        finally:
            self.store.endGroup()

    def _get(self, context, field, default):
        return self._entries().get(context, {}).get(field, default)

    def get(self, context):
        """The service reference typed for `context`, or "" (look for one)."""
        return self._get(context, "ref", "")

    def get_via(self, context):
        """"forward" when `context`'s service is reached through a port-forward, else "proxy"."""
        return self._get(context, "via", "proxy")

    def get_local(self, context):
        """The local port asked for the forward (0 = a free one)."""
        return self._get(context, "local", 0)

    def get_tls(self, context):
        """"on", "off" or "" (use the Preferences setting) for SSL through the forward."""
        return self._get(context, "tls", "")

    def get_insecure(self, context):
        """"on", "off" or "" (use the Preferences setting) for ignoring certificate errors through the forward."""
        return self._get(context, "insecure", "")

    def set(self, context, text, via="proxy", local=0, tls="", insecure=""):
        """Keep `text` for `context`; "" forgets it. Raises OSError when the settings file cannot be written."""
        entries = self._entries()
        text = str(text).strip()
        if text:
            entries[context] = {"ref": text, "via": "forward" if via == "forward" else "proxy", "local": int(local or 0),
                                "tls": _on_off(tls), "insecure": _on_off(insecure)}
        else:
            entries.pop(context, None)
        self.store.remove(self.GROUP)
        for number, (name, entry) in enumerate(entries.items()):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", name)
            self.store.setValue(f"{self.GROUP}/ref.{number}", entry["ref"])
            if entry["via"] == "forward":
                self.store.setValue(f"{self.GROUP}/via.{number}", "forward")
                self.store.setValue(f"{self.GROUP}/local.{number}", entry["local"])
                if entry["tls"]:
                    self.store.setValue(f"{self.GROUP}/tls.{number}", entry["tls"])
                if entry["insecure"]:
                    self.store.setValue(f"{self.GROUP}/insecure.{number}", entry["insecure"])
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")
