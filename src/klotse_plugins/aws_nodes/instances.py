"""What AWS says about an instance, kept for good.

An instance's AMI, launch time, zone and whether it is spot never change once it exists, and an instance id is never reused.
So each instance is asked about once, ever: the answers are kept in memory and in a file per region in the plugin's cache
folder (so they survive restarts, and are there without credentials). Only prices change, and those are not kept here.

The files do not grow without end: an entry that has not been used for UNSEEN_DAYS is dropped when the file is next written.
That loses nothing, since an instance that is not seen for months has been terminated (and, if it is somehow still running,
it is simply asked about once more).
"""
import json
import os
import threading
import time
from pathlib import Path

UNSEEN_DAYS = 180
TOUCH_AFTER_S = 86400  # "last used" is only rewritten when it is this old, so reading does not mean writing every time
FIELDS = ("ami", "launched", "spot", "zone")


class InstanceCache:
    """Instance details by region, in memory and on disk (`cache_dir` None keeps them in memory only)."""

    def __init__(self, now=time.time):
        self._now = now
        self._regions = {}  # (cache dir, region) -> {instance id: {FIELDS..., "seen": epoch}}
        self._lock = threading.Lock()

    @staticmethod
    def _path(cache_dir, region):
        return Path(cache_dir) / f"instances-{region}.json" if cache_dir else None

    def _entries(self, cache_dir, region):
        key = (str(cache_dir) if cache_dir else None, region)
        if key not in self._regions:
            entries, path = {}, self._path(cache_dir, region)
            if path is not None and path.is_file():
                try:
                    stored = json.loads(path.read_text(encoding="utf-8"))
                    entries = {k: v for k, v in stored.items() if isinstance(v, dict) and all(f in v for f in FIELDS)}
                except (OSError, ValueError, AttributeError):
                    entries = {}  # a damaged cache only costs the lookups again
            self._regions[key] = entries
        return self._regions[key]

    def lookup(self, cache_dir, region, instance_ids):
        """(details of the instances already known, ids not known yet)."""
        found, missing, touched = {}, [], False
        with self._lock:
            entries, now = self._entries(cache_dir, region), self._now()
            for instance_id in sorted(set(instance_ids)):
                entry = entries.get(instance_id)
                if entry is None:
                    missing.append(instance_id)
                    continue
                found[instance_id] = {f: entry[f] for f in FIELDS}
                if now - entry.get("seen", 0) > TOUCH_AFTER_S:
                    entry["seen"], touched = now, True
            if touched:
                self._write(cache_dir, region, entries)
        return found, missing

    def store(self, cache_dir, region, details):
        """Keep what AWS said about instances (only those it knew: one that is gone is not remembered as missing)."""
        if not details:
            return
        with self._lock:
            entries, now = self._entries(cache_dir, region), self._now()
            for instance_id, detail in details.items():
                entries[instance_id] = {**{f: detail.get(f) for f in FIELDS}, "seen": now}
            self._write(cache_dir, region, entries)

    def _write(self, cache_dir, region, entries):
        cutoff = self._now() - UNSEEN_DAYS * 86400
        for instance_id in [i for i, e in entries.items() if e.get("seen", 0) < cutoff]:
            del entries[instance_id]
        path = self._path(cache_dir, region)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(entries), encoding="utf-8")
            os.replace(temporary, path)  # a crash mid-write leaves the old file, never half of a new one
        except OSError:
            pass  # not being able to save only costs the lookups again next time
