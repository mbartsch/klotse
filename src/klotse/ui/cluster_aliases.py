from PySide6.QtCore import QSettings

from .settings_store import default_store


class ClusterAliases:
    """Display-only names for kubeconfig contexts, stored in Klotse settings."""

    GROUP = "aliases"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()
        self.aliases = {}
        self._load()

    def _load(self):
        self.store.beginGroup(self.GROUP)
        try:
            contexts = {key.rsplit(".", 1)[1]: str(self.store.value(key) or "").strip()
                        for key in self.store.childKeys() if key.startswith("context.")}
            for number, context in contexts.items():
                alias = str(self.store.value(f"name.{number}") or "").strip()
                if context and alias:
                    self.aliases[context] = alias
        finally:
            self.store.endGroup()

    def display(self, context):
        return self.aliases.get(context, context)

    def set(self, context, alias):
        alias = str(alias or "").strip()
        if alias and alias != context:
            self.aliases[context] = alias
        else:
            self.aliases.pop(context, None)
        self.save()

    def save(self):
        self.store.beginGroup(self.GROUP)
        try:
            for key in self.store.childKeys():
                self.store.remove(key)
            for number, (context, alias) in enumerate(sorted(self.aliases.items())):
                self.store.setValue(f"context.{number}", context)
                self.store.setValue(f"name.{number}", alias)
            self.store.sync()
            if self.store.status() != QSettings.NoError:
                raise OSError(f"Could not save cluster aliases to {self.store.fileName()}")
        finally:
            self.store.endGroup()
