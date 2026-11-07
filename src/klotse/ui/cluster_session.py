from PySide6.QtCore import QSettings

from .settings_store import default_store


class ClusterSession:
    """The clusters to reconnect when Klotse starts, in the shared settings file.

    Nothing is connected automatically: this is written only when the user answers Yes to "remember the open
    clusters?" while exiting, and cleared when they answer No (or exit with no cluster open), so the next start
    begins with a clean list. Plain INI, numbered keys like the other lists:

        [session]
        clusters.0=prod-eu
        clusters.1=staging
        current=staging
        namespaces.0=payments
        namespaces.1=All namespaces

    `namespaces.N` is the namespace that was chosen in `clusters.N` at exit (absent = none recorded).
    """

    GROUP = "session"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _numbered(self, prefix):
        """{number: value} of the keys `prefix.N` in the session group."""
        self.store.beginGroup(self.GROUP)
        try:
            return {int(key.split(".", 1)[1]): self.store.value(key) for key in self.store.childKeys()
                    if key.startswith(prefix + ".") and key.split(".", 1)[1].isdigit()}
        finally:
            self.store.endGroup()

    def saved(self):
        """(remembered contexts in order, the one that was in front or None)."""
        numbered = sorted(self._numbered("clusters").items())
        self.store.beginGroup(self.GROUP)
        try:
            current = self.store.value("current", "")
        finally:
            self.store.endGroup()
        contexts = [str(value) for _, value in numbered if value]
        return contexts, (str(current) or None)

    def namespaces(self):
        """{context: namespace} remembered with the session; clusters without one are left out."""
        clusters, spaces = self._numbered("clusters"), self._numbered("namespaces")
        return {str(clusters[n]): str(spaces[n]) for n in sorted(spaces) if n in clusters and clusters[n] and spaces[n]}

    def remember(self, contexts, current=None, namespaces=None):
        self._write(lambda: self._set(contexts, current, namespaces or {}))

    def forget(self):
        if self.store.contains(f"{self.GROUP}/current") or any(
                key.startswith(f"{self.GROUP}/") for key in self.store.allKeys()):  # nothing saved: write nothing
            self._write(lambda: None)

    def _set(self, contexts, current, namespaces):
        for number, context in enumerate(contexts):
            self.store.setValue(f"{self.GROUP}/clusters.{number}", context)
            if namespaces.get(context):
                self.store.setValue(f"{self.GROUP}/namespaces.{number}", namespaces[context])
        if current:
            self.store.setValue(f"{self.GROUP}/current", current)

    def _write(self, change):
        self.store.remove(self.GROUP)  # always start from nothing so a shorter list leaves no stale entries
        change()
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the session to {self.store.fileName()}")
