"""Big-cluster mode per cluster: which contexts are big (many nodes or pods), so Klotse reads them in a lighter way (see
`KubeService.set_big`). A cluster is marked when its Overview shows it is big, or by the user (View > Big cluster mode); the mark
is kept in the settings file so the next connection is light from the first load. An explicit choice of the user always wins over
the automatic one. Nothing here talks to a cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store

BIG_NODES = 50
BIG_PODS = 1500
MIN_REFRESH_S = 30  # in big-cluster mode the heavy lists (Overview, workloads, pods) reload by themselves no more often than this
HEAVY_VIEWS = ("overview", "workloads", "pods")


def looks_big(nodes, pods):
    return nodes >= BIG_NODES or pods >= BIG_PODS


class BigClusters:
    """`[big_clusters]` in the settings file: `ctx.N` with `mode.N`, where mode is `auto` (found big by Klotse), `on` or `off` (the
    user's choice). No entry = not known to be big."""

    GROUP = "big_clusters"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _entries(self):
        self.store.beginGroup(self.GROUP)
        try:
            keys = self.store.childKeys()
            numbers = sorted(int(k.split(".", 1)[1]) for k in keys if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            return {str(self.store.value(f"ctx.{n}")): str(self.store.value(f"mode.{n}", "auto")) for n in numbers if self.store.value(f"ctx.{n}")}
        finally:
            self.store.endGroup()

    def mode(self, context):
        """"auto", "on", "off" or "" (unknown)."""
        return self._entries().get(context, "")

    def is_big(self, context):
        return self.mode(context) in ("auto", "on")

    def set_mode(self, context, mode):
        """Remember `mode` ("auto", "on", "off", or "" to forget). Raises OSError when the file cannot be written."""
        entries = self._entries()
        if mode:
            entries[context] = mode
        else:
            entries.pop(context, None)
        self.store.remove(self.GROUP)
        for number, (name, value) in enumerate(entries.items()):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", name)
            self.store.setValue(f"{self.GROUP}/mode.{number}", value)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the big-cluster setting to {self.store.fileName()}")

    def detected(self, context):
        """The Overview showed `context` is big: remember it unless the user already chose. True when this is news."""
        if self.mode(context):
            return False
        self.set_mode(context, "auto")
        return True
