"""Search across kinds and namespaces: the query language and the matching, no Qt and no network.

A query is words separated by spaces. Every word must be found in the object's name (case-insensitive). Special terms:
  `key=value`  the label `key` has exactly that value (a word without `=` is part of the name)
  `kind:pod`   only that kind (several `kind:` terms add up; the name is matched case-insensitively, plural or singular)
  `ns:shop`    only that namespace (several add up)
The service lists only metadata (PartialObjectMetadataList), so no Secret data or pod spec is transferred, and stops at a bounded
number of hits; see `KubeService.search_objects`."""
from dataclasses import dataclass, field

# (kind, API client key, method infix, namespaced). Secrets are searched by metadata only: their data is never requested.
SEARCH_KINDS = (
    ("Pod", "core", "pod", True), ("Deployment", "apps", "deployment", True), ("StatefulSet", "apps", "stateful_set", True),
    ("DaemonSet", "apps", "daemon_set", True), ("ReplicaSet", "apps", "replica_set", True), ("Job", "batch", "job", True),
    ("CronJob", "batch", "cron_job", True), ("Service", "core", "service", True), ("Ingress", "net", "ingress", True),
    ("ConfigMap", "core", "config_map", True), ("Secret", "core", "secret", True),
    ("PersistentVolumeClaim", "core", "persistent_volume_claim", True), ("ServiceAccount", "core", "service_account", True),
    ("Namespace", "core", "namespace", False), ("Node", "core", "node", False),
)
KIND_BY_WORD = {}
for _kind, *_ in SEARCH_KINDS:
    for _word in (_kind.lower(), _kind.lower() + "s", _kind.lower() + "es"):
        KIND_BY_WORD[_word] = _kind
KIND_BY_WORD.update({"pvc": "PersistentVolumeClaim", "pvcs": "PersistentVolumeClaim", "sa": "ServiceAccount",
                     "cm": "ConfigMap", "svc": "Service", "ing": "Ingress", "deploy": "Deployment", "sts": "StatefulSet",
                     "ds": "DaemonSet", "rs": "ReplicaSet", "ns": "Namespace", "po": "Pod", "cj": "CronJob"})
HIT_LIMIT = 200  # matches shown at most
PAGE_LIMIT = 500  # objects per API page
MAX_PAGES = 20  # pages per kind: 10,000 objects of one kind at most are looked through


@dataclass
class Query:
    words: list = field(default_factory=list)  # lower-case name fragments, all must match
    labels: list = field(default_factory=list)  # [(key, value)] exact label matches
    kinds: list = field(default_factory=list)  # kind names; empty = every searched kind
    namespaces: list = field(default_factory=list)  # empty = every namespace
    unknown_kinds: list = field(default_factory=list)  # `kind:` words that name nothing searchable (reported, not ignored)

    @property
    def empty(self):
        return not (self.words or self.labels or self.kinds or self.namespaces)

    @property
    def label_selector(self):
        """The server-side selector for the label terms (less to transfer); the hits are checked again here."""
        return ",".join(f"{k}={v}" for k, v in self.labels)


def parse_query(text):
    q = Query()
    for term in str(text).split():
        low = term.lower()
        if low.startswith("kind:") and len(term) > 5:
            kind = KIND_BY_WORD.get(low[5:])
            if kind is None:
                q.unknown_kinds.append(term[5:])
            elif kind not in q.kinds:
                q.kinds.append(kind)
        elif low.startswith("ns:") and len(term) > 3:
            q.namespaces.append(term[3:])
        elif "=" in term and not term.startswith("="):
            key, _, value = term.partition("=")
            q.labels.append((key, value))
        else:
            q.words.append(low)
    return q


def kinds_for(query):
    """The (kind, api, infix, namespaced) tuples to list for `query`: the named kinds, or all. With a `ns:` term the cluster-wide
    kinds (Namespace, Node) are left out unless they are named, since they have no namespace to match."""
    chosen = [k for k in SEARCH_KINDS if not query.kinds or k[0] in query.kinds]
    if query.namespaces and not query.kinds:
        chosen = [k for k in chosen if k[3]]
    return chosen


def matches(meta, query):
    """True when an object's metadata dict (name, namespace, labels) fits the query."""
    name = (meta.get("name") or "").lower()
    if any(w not in name for w in query.words):
        return False
    if query.namespaces and meta.get("namespace") not in query.namespaces:
        return False
    labels = meta.get("labels") or {}
    return all(labels.get(k) == v for k, v in query.labels)


@dataclass
class Hit:
    kind: str
    namespace: str
    name: str
    created: str = ""  # creationTimestamp as the API sent it
    labels: dict = field(default_factory=dict)

    def label_text(self, limit=120):
        text = ", ".join(f"{k}={v}" for k, v in sorted(self.labels.items()))
        return text if len(text) <= limit else text[:limit - 1] + "…"
