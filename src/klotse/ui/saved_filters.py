from PySide6.QtCore import QSettings

from .settings_store import default_store

FIELDS = ("ctx", "kind", "name", "text")


class SavedFilters:
    """Filter texts the user kept, per cluster and list kind. Plain INI with numbered keys like the other lists:

        [saved_filters]
        ctx.0=prod-eu
        kind.0=Pod
        name.0=crashing
        text.0=CrashLoop
    """

    GROUP = "saved_filters"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def all(self):
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys()
                             if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            out = []
            for n in numbers:
                entry = {f: str(self.store.value(f"{f}.{n}") or "") for f in FIELDS}
                if entry["ctx"] and entry["kind"] and entry["name"] and entry["text"]:
                    out.append(entry)
            return out
        finally:
            self.store.endGroup()

    def for_list(self, context, kind):
        return [e for e in self.all() if e["ctx"] == context and e["kind"] == kind]

    def _write(self, entries):
        self.store.remove(self.GROUP)
        for n, entry in enumerate(entries):
            for f in FIELDS:
                self.store.setValue(f"{self.GROUP}/{f}.{n}", entry[f])
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the saved filters to {self.store.fileName()}")

    def add(self, context, kind, name, text):
        """Keep `text` under `name` for this cluster's list; the same name replaces the older filter. OSError on a save error."""
        name, text = name.strip(), text.strip()
        if not name or not text:
            raise ValueError("A saved filter needs a name and some filter text.")
        keep = [e for e in self.all() if not (e["ctx"] == context and e["kind"] == kind and e["name"] == name)]
        self._write(keep + [{"ctx": context, "kind": kind, "name": name, "text": text}])

    def remove(self, entry):
        self._write([e for e in self.all() if e != entry])
