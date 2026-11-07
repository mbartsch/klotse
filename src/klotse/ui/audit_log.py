"""A local, append-only record of the changes made from Klotse: when, which cluster, what, to which object, and whether the cluster
accepted it. One JSON object per line in `audit.log` next to the settings file. It never holds object bodies, Secret data, patch
contents or tokens: only the action and the object's identity. Writes are best effort and never raise (an unwritable log must not
stop a change the user asked for)."""
import json
import os
import threading
from datetime import datetime, timezone

from ..k8s.audit import describe, target_of  # noqa: F401 - re-exported for the dialog and tests

MAX_BYTES = 1_000_000  # the file is moved to audit.log.1 (replacing an older one) when it grows past this
FIELDS = ("time", "cluster", "action", "target", "result", "detail")


class AuditLog:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()

    def record(self, cluster, action, target="", result="ok", detail=""):
        entry = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "cluster": cluster, "action": action,
                 "target": target, "result": result, "detail": str(detail).splitlines()[0][:300] if detail else ""}
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            return None
        return entry

    def entries(self, limit=2000):
        """The newest `limit` entries, newest first; damaged lines are skipped."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                lines = handle.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append({f: str(entry.get(f, "")) for f in FIELDS})
        return out

    def clear(self):
        """Delete the log (and its rotated copy). Raises OSError when a file cannot be removed."""
        with self._lock:
            for path in (self.path, self.path + ".1"):
                if os.path.exists(path):
                    os.remove(path)
