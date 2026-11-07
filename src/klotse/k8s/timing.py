"""Optional timing log (a debug option): how long loads take and where the time goes, one JSON object per line in `timings.log` next to
the settings file. Off by default; switched on in Preferences > General ("Write a timing log") or with the environment variable
`KLOTSE_TIMINGS=1`. Only names, counts and seconds are written: never object names, manifests, Secret data, tokens or kubeconfig
text. Writes are best effort and never raise, and when the log is off every call returns at once (a flag test), so the hooks cost
nothing in normal use. The file is moved to `timings.log.1` when it passes 2 MB.

Kinds written: `session` (start line: version, platform), `connect`, `phase` (steps inside one load, such as the overview's
nodes / pods / events), `api-list` (one paged list call: pages, items, seconds spent in the Kubernetes client including its JSON
decoding), `worker` (a background call as the GUI asked for it: `queued` = seconds waiting for a free thread, `seconds` = the call,
`items` = size of the answer), `gui` (the GUI thread's work on an answer: filling a table) and `total` (asking to answer shown)."""
import contextlib
import json
import os
import platform
import threading
import time
from datetime import datetime, timezone

MAX_BYTES = 2_000_000
ENV = "KLOTSE_TIMINGS"


class TimingLog:
    def __init__(self):
        self.path = None
        self.enabled = False
        self._lock = threading.Lock()

    def configure(self, path, enabled=None, version=""):
        """Set the file and switch the log on or off. `enabled=None` follows the KLOTSE_TIMINGS environment variable."""
        if enabled is None:
            enabled = os.environ.get(ENV, "") not in ("", "0")
        elif os.environ.get(ENV, "") not in ("", "0"):
            enabled = True
        was = self.enabled
        self.path, self.enabled = str(path) if path else None, bool(enabled) and bool(path)
        if self.enabled and not was:
            self.record("session", "start", 0.0, klotse=version, python=platform.python_version(), system=platform.platform())
        return self.enabled

    def record(self, kind, label, seconds, **info):
        if not self.enabled:
            return
        entry = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "kind": kind, "label": str(label),
                 "seconds": round(float(seconds), 3)}
        entry.update({k: v for k, v in info.items() if v is not None})
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str)
            with self._lock:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except (OSError, TypeError, ValueError):
            pass  # a log that cannot be written must never get in the way

    @contextlib.contextmanager
    def timed(self, kind, label, **info):
        """`with timings.timed("phase", "overview.pods") as extra:` times the block; `extra` is a dict to add counts to."""
        extra = {}
        if not self.enabled:
            yield extra
            return
        start = time.perf_counter()
        try:
            yield extra
        finally:
            self.record(kind, label, time.perf_counter() - start, **{**info, **extra})


timings = TimingLog()


def name_of(fn):
    """A readable name for a callable (a method, a function, a lambda or a functools.partial); never its arguments."""
    while hasattr(fn, "func"):
        fn = fn.func
    return getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or type(fn).__name__


def size_of(result):
    """Number of items in an answer when it is a list/tuple/dict; for a tuple of lists the sum of their lengths."""
    try:
        if isinstance(result, dict):
            return len(result)
        if isinstance(result, (list, tuple)):
            return sum(len(r) if isinstance(r, (list, tuple, dict)) else 1 for r in result) if result and isinstance(result, tuple) else len(result)
    except TypeError:
        pass
    return None
