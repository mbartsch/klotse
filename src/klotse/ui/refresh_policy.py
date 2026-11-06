"""Which lists reload by themselves and which only when the user asks. Only the **workloads** and the **nodes** (and the cluster
Overview that summarises them) reload automatically by default; every other list (events, services, config, storage, access
control, custom resources, Helm releases, ...) is **manual**: no timer, no change watch, no reload when its tab comes to the
front. A manual list loads once when it is first opened and again from the toolbar Refresh button or the list's right-click
Refresh. Either default can be changed per list from its right-click menu (Refresh automatically) and is remembered in the
settings file; the toolbar's Auto-refresh switch still turns the automatic reloads of all lists off at once."""
from PySide6.QtCore import QSettings

from .settings_store import default_store

DEFAULT_AUTO = frozenset({
    "overview", "nodes",  # the cluster overview and the nodes
    "workloads", "pods", "deployments", "daemonsets", "statefulsets", "replicasets", "jobs", "cronjobs",  # the Workloads group ...
    "horizontalpodautoscalers"})  # ... which also holds the autoscalers that follow the load
DEFAULT_AUTO_PREFIXES = ("node-pods:", "pod:")  # the pods running on one node, and one pod's containers


def default_manual(key):
    """True for every list that is not a workload or a node."""
    return not (key in DEFAULT_AUTO or any(key.startswith(p) for p in DEFAULT_AUTO_PREFIXES))


def is_manual(key, overrides):
    """True when the list `key` reloads only on request: the user's choice if there is one, else the default."""
    return overrides[key] if key in overrides else default_manual(key)


class RefreshSettings:
    """The user's choices that differ from the defaults, plain INI with numbered keys like the other lists:

        [refresh]
        manual.0=pods
        auto.0=roles
    """

    GROUP = "refresh"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()
        self._cache = None

    def overrides(self):
        """{view key: True (manual) / False (automatic)} for the lists whose mode the user changed."""
        if self._cache is None:
            found = {}
            self.store.beginGroup(self.GROUP)
            try:
                for key in self.store.childKeys():
                    kind, _, number = key.partition(".")
                    if kind in ("manual", "auto") and number.isdigit() and self.store.value(key):
                        found[str(self.store.value(key))] = kind == "manual"
            finally:
                self.store.endGroup()
            self._cache = found
        return self._cache

    def _write(self, mapping):
        self.store.remove(self.GROUP)  # start from nothing so a shorter list leaves no stale entries
        for number, (name, is_man) in enumerate(sorted(mapping.items())):
            self.store.setValue(f"{self.GROUP}/{'manual' if is_man else 'auto'}.{number}", name)
        self.store.sync()
        return self.store.status() == QSettings.NoError

    def set_manual(self, key, manual):
        """Choose the mode of one list; a choice equal to the default is not stored. Raises OSError when the settings file
        cannot be written, and the previous choice then stays (in memory and in the settings object)."""
        previous = dict(self.overrides())
        updated = dict(previous)
        if manual == default_manual(key):
            updated.pop(key, None)
        else:
            updated[key] = bool(manual)
        if not self._write(updated):
            self._write(previous)
            raise OSError(f"Could not save the refresh setting to {self.store.fileName()}")
        self._cache = updated
