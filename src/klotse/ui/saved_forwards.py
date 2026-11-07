from PySide6.QtCore import QSettings

from .settings_store import default_store

FIELDS = ("ctx", "kind", "ns", "name", "remote", "local")


class SavedForwards:
    """Port forwards the user chose to keep, per cluster context. Plain INI with numbered keys like the other lists:

        [saved_forwards]
        ctx.0=prod-eu
        kind.0=Service
        ns.0=shop
        name.0=db
        remote.0=5432
        local.0=15432

    `local` 0 means "a free port". A Pod's name changes when the pod is replaced, so a saved Pod forward may stop matching;
    Services are the stable choice."""

    GROUP = "saved_forwards"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def all(self):
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys()
                             if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            out = []
            for n in numbers:
                entry = {f: self.store.value(f"{f}.{n}") for f in FIELDS}
                try:
                    entry["remote"], entry["local"] = int(entry["remote"]), int(entry["local"] or 0)
                except (TypeError, ValueError):
                    continue
                if entry["ctx"] and entry["kind"] in ("Pod", "Service") and entry["ns"] and entry["name"] and 0 < entry["remote"] < 65536:
                    out.append({k: (v if isinstance(v, int) else str(v)) for k, v in entry.items()})
            return out
        finally:
            self.store.endGroup()

    def for_context(self, context):
        return [e for e in self.all() if e["ctx"] == context]

    def _write(self, entries):
        self.store.remove(self.GROUP)
        for n, entry in enumerate(entries):
            for f in FIELDS:
                self.store.setValue(f"{self.GROUP}/{f}.{n}", entry[f])
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the saved port forwards to {self.store.fileName()}")

    @staticmethod
    def make(context, kind, namespace, name, remote, local=0):
        return {"ctx": context, "kind": kind, "ns": namespace, "name": name, "remote": int(remote), "local": int(local or 0)}

    def add(self, entry):
        """Keep `entry` (a `make` dict); the same target and remote port replaces the older entry. Raises OSError on a save error."""
        same = lambda e: all(e[f] == entry[f] for f in ("ctx", "kind", "ns", "name", "remote"))
        self._write([e for e in self.all() if not same(e)] + [entry])

    def remove(self, entry):
        self._write([e for e in self.all() if e != entry])
