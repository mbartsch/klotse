from PySide6.QtCore import QSettings

from .settings_store import default_store

SEPARATOR = "/"


def clean_path(text):
    """A group path as stored: segments trimmed, empty ones dropped ("  a // b " -> "a/b"); "" means no group."""
    parts = [part.strip() for part in str(text or "").split(SEPARATOR)]
    return SEPARATOR.join(part for part in parts if part)


def parent_path(path):
    return path.rpartition(SEPARATOR)[0]


def leaf_name(path):
    return path.rpartition(SEPARATOR)[2]


def lineage(path):
    """The group and its parents, outermost first: "a/b/c" -> ["a", "a/b", "a/b/c"]."""
    parts = path.split(SEPARATOR) if path else []
    return [SEPARATOR.join(parts[:i]) for i in range(1, len(parts) + 1)]


def inside(path, group):
    """True when `path` is `group` itself or lies below it."""
    return path == group or path.startswith(group + SEPARATOR)


class ClusterGroups:
    """Folders for the cluster list: which group (a path such as "prod/eu") each context sits in.

    Saved in the shared settings file, only when `save()` is called, as plain INI with numbered keys:

        [groups]
        empty.0=prod/asia          groups created by hand that currently hold no cluster
        assign.0=arn:aws:eks:...   a context ...
        to.0=prod/eu               ... and the group it is in (paired by number)

    A context that is not listed is ungrouped. Entries for contexts missing from the kubeconfig are kept, so they
    are back in place if the context returns.
    """

    GROUP = "groups"

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()
        self.assignments = {}  # context -> group path
        self.empty = []  # group paths created by hand
        self._load()

    # ---- reading -----------------------------------------------------------
    def _numbered(self, prefix):
        found = {}
        for key in self.store.childKeys():
            head, _, number = key.partition(".")
            if head == prefix and number.isdigit():
                found[int(number)] = str(self.store.value(key) or "")
        return found

    def _load(self):
        self.store.beginGroup(self.GROUP)
        try:
            empty, contexts, targets = self._numbered("empty"), self._numbered("assign"), self._numbered("to")
        finally:
            self.store.endGroup()
        self.empty = [path for path in (clean_path(empty[n]) for n in sorted(empty)) if path]
        for number in sorted(contexts):
            path = clean_path(targets.get(number, ""))
            if contexts[number] and path:
                self.assignments[contexts[number]] = path

    def group_of(self, context):
        return self.assignments.get(context, "")

    def groups(self):
        """Every group, including the parents of nested ones, sorted by path."""
        found = set()
        for path in [*self.empty, *self.assignments.values()]:
            found.update(lineage(path))
        return sorted(found, key=str.casefold)

    def contexts_in(self, group, contexts):
        """The given contexts that are in `group` or below it, in the order given."""
        return [ctx for ctx in contexts if inside(self.group_of(ctx), group)]

    # ---- changing (in memory until save()) ---------------------------------
    def assign(self, context, group):
        group = clean_path(group)
        if group:
            self.assignments[context] = group
        else:
            self.assignments.pop(context, None)

    def add_group(self, group):
        group = clean_path(group)
        if group and group not in self.empty:
            self.empty.append(group)
        return group

    def rename_group(self, old, new):
        """Move a group, and everything below it, to a new path (also merges into an existing group)."""
        new = clean_path(new)
        if not new or new == old:
            return old

        def moved(path):
            return new + path[len(old):] if inside(path, old) else path

        self.assignments = {ctx: moved(path) for ctx, path in self.assignments.items()}
        self.empty = list(dict.fromkeys(moved(path) for path in self.empty))
        return new

    def delete_group(self, group):
        """Remove a group and its subgroups; their clusters move up to the group's parent (or become ungrouped)."""
        parent = parent_path(group)
        self.assignments = {ctx: (parent if inside(path, group) else path) for ctx, path in self.assignments.items()}
        self.assignments = {ctx: path for ctx, path in self.assignments.items() if path}
        self.empty = [path for path in self.empty if not inside(path, group)]

    # ---- saving ------------------------------------------------------------
    def save(self):
        self.store.remove(self.GROUP)
        for number, path in enumerate(self.empty):
            self.store.setValue(f"{self.GROUP}/empty.{number}", path)
        for number, (context, path) in enumerate(self.assignments.items()):
            self.store.setValue(f"{self.GROUP}/assign.{number}", context)
            self.store.setValue(f"{self.GROUP}/to.{number}", path)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the cluster groups to {self.store.fileName()}")
