"""Big-cluster mode per cluster: which contexts are big (many nodes or pods), so Klotse reads them in a lighter way (see
`KubeService.set_big`). A cluster is marked when its Overview shows it is big, or by the user (View > Big cluster mode); the mark
is kept in the settings file so the next connection is light from the first load. An explicit choice of the user always wins over
the automatic one. Nothing here talks to a cluster."""
from PySide6.QtCore import QSettings

from .settings_store import default_store

BIG_NODES = 50
BIG_PODS = 1500

NOTICE_TEXT = (
    "This cluster is big, so Klotse reads it in a lighter way. What changes compared with a small cluster:\n\n"
    "\u2022 Auto-refresh waits the chosen time after the last refresh finished, instead of reloading every N seconds.\n"
    "\u2022 The Overview shows Warning events only, and from 5,000 pods counts pods by phase (a crash-looping pod counts as running; "
    "Health check finds those).\n"
    "\u2022 With 5,000 or more pods the Pods list loads 2,000 at a time (Load more) and refreshes only on request; filter and sort "
    "cover the loaded pods. View > Search objects\u2026 searches them all.\n"
    "\u2022 The Pods column of the Nodes tab is recounted every 5 minutes.\n"
    "\u2022 Health check, Capacity and similar windows read their data side by side.\n\n"
    "View > Big cluster mode switches this off for this cluster.")


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

    # ---- the "this cluster is read in a lighter way" notice, hidden per cluster -------------------------------------
    NOTICE_GROUP = "big_notice"

    def _hidden(self):
        self.store.beginGroup(self.NOTICE_GROUP)
        try:
            numbered = sorted((int(k.split(".", 1)[1]), self.store.value(k)) for k in self.store.childKeys()
                              if k.startswith("contexts.") and k.split(".", 1)[1].isdigit())
        finally:
            self.store.endGroup()
        return [str(v) for _, v in numbered if v]

    def notice_hidden(self, context):
        return context in self._hidden()

    def hide_notice(self, context, hidden=True):
        """Do not show the notice again for `context` (or show it again with hidden=False). Raises OSError when not saved."""
        current = [c for c in self._hidden() if c != context]
        if hidden:
            current.append(context)
        self.store.remove(self.NOTICE_GROUP)
        for number, name in enumerate(current):
            self.store.setValue(f"{self.NOTICE_GROUP}/contexts.{number}", name)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")

    def detected(self, context):
        """The Overview showed `context` is big: remember it unless the user already chose. True when this is news."""
        if self.mode(context):
            return False
        self.set_mode(context, "auto")
        return True
