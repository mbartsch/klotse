"""In-memory index of the API resources a cluster serves (built-in and custom), for pickers such as the role editor.

A cluster can serve a very large number of custom resource types, so nothing here builds a widget or a list per
item for the UI: `search` scans a compact list and stops after `limit` hits. No Qt, no network."""
from typing import NamedTuple


class ApiResource(NamedTuple):
    group: str  # "" = the core group
    name: str  # plural, or "plural/subresource" such as "pods/log"
    kind: str
    namespaced: bool
    version: str = ""


class ApiIndex:
    def __init__(self):
        self._items = []
        self._keys = []  # lower-case search text, parallel to _items
        self._seen = set()
        self._groups = set()
        self._group_list = []
        self._dirty = False

    def __len__(self):
        return len(self._items)

    def clear(self):
        self.__init__()

    def add(self, resources):
        """Add resources (duplicates of group+name are ignored); returns how many were new."""
        added = 0
        for r in resources:
            key = (r.group, r.name)
            if key in self._seen:
                continue
            self._seen.add(key)
            self._items.append(r)
            self._keys.append(f"{r.group}\0{r.name}\0{r.kind}".lower())
            self._groups.add(r.group)
            added += 1
        if added:
            self._dirty = True
        return added

    def _ordered(self):
        """Core group first, then by group and name; sorted lazily and only when something was added."""
        if self._dirty:
            order = sorted(range(len(self._items)), key=lambda i: (self._items[i].group != "", self._items[i].group,
                                                                    self._items[i].name))
            self._items = [self._items[i] for i in order]
            self._keys = [self._keys[i] for i in order]
            self._group_list = sorted(self._groups, key=lambda g: (g != "", g))
            self._dirty = False
        return self._items

    @property
    def group_count(self):
        return len(self._groups)

    def has_group(self, group):
        return group in self._groups

    def search(self, query="", groups=(), limit=200):
        """Resources whose group, name or kind contain every word of `query`, limited to `groups` when given.
        Returns (hits, more): `more` is True when there were further matches than `limit`."""
        items = self._ordered()
        words = query.lower().split()
        wanted = set(groups)
        hits = []
        for item, key in zip(items, self._keys):
            if wanted and item.group not in wanted:
                continue
            if words and not all(w in key for w in words):
                continue
            if len(hits) == limit:
                return hits, True
            hits.append(item)
        return hits, False

    def search_groups(self, query="", limit=200):
        """API group names containing `query` (the core group is "" and matches "core" too)."""
        self._ordered()
        q = query.lower().strip()
        hits = []
        for g in self._group_list:
            if q and q not in g.lower() and not (g == "" and q in "core"):
                continue
            if len(hits) == limit:
                return hits, True
            hits.append(g)
        return hits, False
