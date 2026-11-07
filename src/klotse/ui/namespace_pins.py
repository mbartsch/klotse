from PySide6.QtCore import QSettings

from .settings_store import default_store


class NamespacePins:
    """Namespaces the user pinned, per cluster context. Plain INI with numbered keys like the other lists:

        [pins]
        ctx.0=prod-eu
        ns.0=payments
        ctx.1=prod-eu
        ns.1=kube-system
    """

    GROUP = "pins"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _pairs(self):
        self.store.beginGroup(self.GROUP)
        try:
            keys = self.store.childKeys()
            numbers = sorted(int(k.split(".", 1)[1]) for k in keys if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            return [(str(self.store.value(f"ctx.{n}") or ""), str(self.store.value(f"ns.{n}") or "")) for n in numbers]
        finally:
            self.store.endGroup()

    def pinned(self, context):
        return [ns for ctx, ns in self._pairs() if ctx == context and ns]

    def is_pinned(self, context, namespace):
        return namespace in self.pinned(context)

    def set_pinned(self, context, namespace, on):
        """Pin or unpin; raises OSError when the settings file cannot be written (the caller keeps the old state)."""
        pairs = [(c, n) for c, n in self._pairs() if c and n and not (c == context and n == namespace)]
        if on:
            pairs.append((context, namespace))
        self.store.remove(self.GROUP)
        for number, (ctx, ns) in enumerate(pairs):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", ctx)
            self.store.setValue(f"{self.GROUP}/ns.{number}", ns)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the pinned namespaces to {self.store.fileName()}")
