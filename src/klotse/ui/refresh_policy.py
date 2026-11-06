"""Which lists reload by themselves and which only when the user asks. Definitions that rarely change (CRDs and their groups,
Storage and Priority Classes, webhook configurations, Access Control) are **manual** by default: no timer, no change watch,
no reload when their tab comes to the front; they load once when first opened and again from the toolbar Refresh button or the
list's right-click Refresh. Everything else (pods, nodes, workloads, events, ...) reloads automatically as before. A list's
mode can be changed from its right-click menu and is remembered in the settings file."""
from PySide6.QtCore import QSettings

from .settings_store import default_store

DEFAULT_MANUAL = frozenset({
    "crds", "crd-groups", "storageclasses", "priorityclasses", "validatingwebhookconfigurations", "mutatingwebhookconfigurations",
    "serviceaccounts", "roles", "rolebindings", "clusterroles", "clusterrolebindings"})
DEFAULT_MANUAL_PREFIXES = ("crd-group:",)  # the tab of one CRD group


def default_manual(key):
    return key in DEFAULT_MANUAL or any(key.startswith(p) for p in DEFAULT_MANUAL_PREFIXES)


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
