from collections import deque

from PySide6.QtCore import QObject, Signal


class ApiIndexLoader(QObject):
    """Fills an ApiIndex from the cluster's API discovery in the background.

    One call lists the groups, then the groups are fetched in small batches by a few parallel lanes, each batch in one
    worker (`run` is `Workspace._run`, so results for a closed workspace are dropped). Whatever has arrived is already
    searchable; `progress` fires after every batch. A group that fails is counted and skipped, never fatal."""

    progress = Signal(int, int)  # groups done, groups total
    finished = Signal()

    BATCH = 20
    LANES = 4

    def __init__(self, service, run, index, parent=None):
        super().__init__(parent)
        self.service, self._run, self.index = service, run, index
        self.state = "idle"  # idle | loading | done | failed
        self.error = ""
        self.done = self.total = self.failed_groups = 0
        self._queue = deque()
        self._active = 0

    def start(self, force=False):
        if self.state == "loading" or (self.state == "done" and not force):
            return
        if force:
            self.index.clear()
        self.state, self.error = "loading", ""
        self.done = self.total = self.failed_groups = 0
        self._run(self.service.discovery_groups, self._got_groups, on_error=self._groups_failed)

    def _groups_failed(self, message):
        self.state, self.error = "failed", message
        self.finished.emit()

    def _got_groups(self, groups):
        self._queue = deque(groups)
        self.total = len(self._queue)
        self.progress.emit(0, self.total)
        if not self._queue:
            self._finish()
            return
        for _ in range(self.LANES):
            self._next()

    def _next(self):
        if not self._queue:
            if self._active == 0 and self.state == "loading":
                self._finish()
            return
        batch = [self._queue.popleft() for _ in range(min(self.BATCH, len(self._queue)))]
        service = self.service
        self._active += 1

        def fetch():
            found, failed = [], 0
            for group, version in batch:
                try:
                    found.extend(service.discovery_resources(group, version))
                except Exception:  # one unreachable aggregated API must not hide the others
                    failed += 1
            return found, failed

        self._run(fetch, lambda result, n=len(batch): self._batch_done(result, n),
                  on_error=lambda _m, n=len(batch): self._batch_done(([], n), n))

    def _batch_done(self, result, count):
        found, failed = result
        self._active -= 1
        self.index.add(found)
        self.done += count
        self.failed_groups += failed
        self.progress.emit(self.done, self.total)
        self._next()

    def _finish(self):
        self.state = "done"
        self.finished.emit()

    def status_text(self):
        if self.state == "failed":
            return f"Could not list the API resources: {self.error}"
        count = f"{len(self.index):,} resources in {self.index.group_count:,} API groups"
        if self.state == "loading":
            return f"Loading API resources… {self.done:,} of {self.total:,} groups ({count} so far)"
        skipped = f"; {self.failed_groups} group(s) could not be read" if self.failed_groups else ""
        return f"{count}{skipped}"
