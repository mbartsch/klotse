"""Read-only mode per cluster: which contexts the user marked as "look, do not touch" (production, say). The mark is kept in
the settings file so it is on again at the next start. Nothing here talks to a cluster: the workspace refuses changes while
it is on, and KubeService refuses again underneath (`k8s.client.mutating`)."""
from PySide6.QtCore import QSettings

from .settings_store import default_store


class ReadOnlyClusters:
    """The read-only contexts, plain INI with numbered keys like the other lists:

        [readonly]
        contexts.0=prod-eu
        contexts.1=prod-us
    """

    GROUP = "readonly"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def contexts(self):
        self.store.beginGroup(self.GROUP)
        try:
            numbered = sorted((int(key.split(".", 1)[1]), self.store.value(key)) for key in self.store.childKeys()
                              if key.startswith("contexts.") and key.split(".", 1)[1].isdigit())
        finally:
            self.store.endGroup()
        return [str(value) for _, value in numbered if value]

    def is_read_only(self, context):
        return context in self.contexts()

    def set_read_only(self, context, on):
        """Mark or unmark `context`; raises OSError when the settings file cannot be written (nothing is then changed in
        memory either: the caller keeps the old state)."""
        current = [c for c in self.contexts() if c != context]
        if on:
            current.append(context)
        self.store.remove(self.GROUP)  # start from nothing so a shorter list leaves no stale entries
        for number, name in enumerate(current):
            self.store.setValue(f"{self.GROUP}/contexts.{number}", name)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the read-only setting to {self.store.fileName()}")
