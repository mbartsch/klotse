from PySide6.QtCore import QObject, Signal


class AccountDirectory(QObject):
    """The cluster's service accounts as (namespace, name), loaded once in the background (`run` is `Workspace._run`)
    for the binding editor's picker. `search` returns at most `limit` matches, so a large cluster never fills a widget."""

    finished = Signal()
    _call = "list_service_account_names"  # the KubeService method that lists the entries

    def _fetch_args(self):
        return ()

    def __init__(self, service, run, parent=None):
        super().__init__(parent)
        self.service, self._run = service, run
        self.entries = []
        self.state = "idle"  # idle | loading | done | failed
        self.error = ""

    def start(self, force=False):
        if self.state == "loading" or (self.state == "done" and not force):
            return
        self.state, self.error = "loading", ""
        self._run(getattr(self.service, self._call), self._got, *self._fetch_args(), on_error=self._failed)

    def _got(self, entries):
        self.entries = sorted(set(entries))
        self.state = "done"
        self.finished.emit()

    def _failed(self, message):
        self.state, self.error = "failed", message
        self.finished.emit()

    def search(self, query="", limit=200):
        """Entries where every word of `query` is in "namespace/name". Returns (hits, more)."""
        words = query.lower().replace("/", " ").split()
        hits = []
        for ns, name in self.entries:
            text = f"{ns}/{name}".lower()
            if words and not all(w in text for w in words):
                continue
            if len(hits) == limit:
                return hits, True
            hits.append((ns, name))
        return hits, False

    def status_text(self):
        if self.state == "failed":
            return f"Could not list the service accounts: {self.error}"
        if self.state == "loading":
            return "Loading service accounts…"
        return f"{len(self.entries):,} service accounts"


class SecretDirectory(AccountDirectory):
    """The names of the secrets of one namespace (names only, no secret data), loaded in the background like the
    service accounts; entries are plain names."""

    _call = "list_secret_names"

    def __init__(self, service, run, namespace, parent=None):
        super().__init__(service, run, parent)
        self.namespace = namespace

    def _fetch_args(self):
        return (self.namespace,)

    def search(self, query="", limit=200):
        words = query.lower().split()
        hits = []
        for name in self.entries:
            if words and not all(w in name.lower() for w in words):
                continue
            if len(hits) == limit:
                return hits, True
            hits.append(name)
        return hits, False

    def status_text(self):
        if self.state == "failed":
            return f"Could not list the secrets: {self.error}. You can still type a name."
        if self.state == "loading":
            return "Loading secret names…"
        return f"{len(self.entries):,} secrets in {self.namespace}"
