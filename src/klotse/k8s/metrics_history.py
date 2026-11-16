"""Usage history for the metrics chart (pure, no Qt, no network).

metrics-server only knows *now*: one sample per object, refreshed about every 15 to 60 seconds, and keeps no history. So when no Prometheus
or VictoriaMetrics is available the chart builds its own history: it samples metrics-server while the chart window is open and keeps the
points in memory (`History`), stamped with the time metrics-server itself gives them, so a sample read twice is stored once. The history
starts when the window opens and is gone when it closes; the window says so.

`parse_sample` turns a metrics.k8s.io object into numbers (CPU in cores, memory in MiB); `scope_series` names the lines a chart draws for a
pod (one per container and the total), a node (one line) or a workload (the total and one per pod)."""
from collections import deque
from datetime import datetime, timezone

from kubernetes.utils import parse_quantity

MAX_POINTS = 4000  # per line: about 16 hours at one sample a minute, 4 hours at 15 seconds
MAX_LINES = 9  # lines on a chart: the total and the first few pods or containers
MAX_OBJECTS = 12  # objects picked together in one usage window (one line each, plus a total)
MAX_SERIES = 64  # includes recently disappeared pods; bounds churn, not only the current sample
MAX_AGE_S = 24 * 60 * 60  # expire every line by receipt age, independently of server clock skew


def _cores(value):
    try:
        return float(parse_quantity(value or 0))
    except (ValueError, ArithmeticError):
        return 0.0


def _mib(value):
    try:
        return float(parse_quantity(value or 0)) / 2**20
    except (ValueError, ArithmeticError):
        return 0.0


def parse_time(text):
    """Epoch seconds of a metrics-server `timestamp` (RFC 3339), or None."""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def parse_pod(item):
    """(timestamp, {container: (cores, MiB)}) of one PodMetrics object."""
    containers = {c.get("name", "?"): (_cores((c.get("usage") or {}).get("cpu")), _mib((c.get("usage") or {}).get("memory")))
                  for c in item.get("containers") or []}
    return parse_time(item.get("timestamp")), containers


def parse_node(item):
    """(timestamp, (cores, MiB)) of one NodeMetrics object."""
    usage = item.get("usage") or {}
    return parse_time(item.get("timestamp")), (_cores(usage.get("cpu")), _mib(usage.get("memory")))


def pod_sample(item):
    """Lines for one pod: each container, and `total` when there are several. {line: (timestamp, cores, MiB)}."""
    stamp, containers = parse_pod(item)
    lines = {name: (stamp, cpu, mem) for name, (cpu, mem) in containers.items()}
    if len(containers) > 1:
        lines["total"] = (stamp, sum(c for c, _ in containers.values()), sum(m for _, m in containers.values()))
    return lines


def node_sample(item):
    stamp, (cpu, mem) = parse_node(item)
    return {"node": (stamp, cpu, mem)}


def workload_sample(items):
    """Lines for a set of pods (a workload): `total`, and one line per pod while there are few of them."""
    lines, cpu, mem, stamp = {}, 0.0, 0.0, None
    for item in items:
        t, containers = parse_pod(item)
        pod_cpu, pod_mem = sum(c for c, _ in containers.values()), sum(m for _, m in containers.values())
        cpu, mem = cpu + pod_cpu, mem + pod_mem
        stamp = max([x for x in (stamp, t) if x is not None], default=None)
        lines[item.get("metadata", {}).get("name", "?")] = (t, pod_cpu, pod_mem)
    if len(lines) > MAX_LINES - 1:
        lines = {}
    lines["total"] = (stamp, cpu, mem)
    return lines


def combine(parts):
    """Several objects picked together (usage window): `parts` is [(label, sample)] where each sample is what one object's sample
    function gave ({line: (t, cpu, mem)}). Each object becomes one line, its `total` (or its only line); with two or more objects a
    `total` line adds them up. An object with no reading yet is left out. Returns {line: (t, cpu, mem)}."""
    lines, cpu, mem, stamp = {}, 0.0, 0.0, None
    for label, sample in parts:
        if not sample:
            continue
        picked = sample.get("total") or (next(iter(sample.values())) if len(sample) == 1 else None)
        if picked is None:
            continue
        t, c, m = picked
        lines[label] = (t, c, m)
        cpu, mem = cpu + c, mem + m
        stamp = max([x for x in (stamp, t) if x is not None], default=None)
    if len(lines) > 1:
        lines["total"] = (stamp, cpu, mem)
    return lines


class History:
    """Bounded points per line: `{line: deque of (t, cpu, mem)}`; duplicate/older timestamps are ignored.

    Parallel receipt times bound the lifetime of every point, including inactive lines, without trusting the metrics clock."""

    def __init__(self, max_points=MAX_POINTS, max_series=MAX_SERIES, max_age_s=MAX_AGE_S):
        if max_points < 1 or max_series < 1 or max_age_s <= 0:
            raise ValueError("History bounds must be positive")
        self.max_points, self.max_series, self.max_age_s = max_points, max_series, max_age_s
        self.lines, self._receipts, self._last_seen = {}, {}, {}
        self._clock = None

    def add(self, sample, fallback_time):
        """Store `sample` ({line: (t, cpu, mem)}); `fallback_time` stands in for a sample without a timestamp. Returns how many lines
        got a new point."""
        # Keep expiry on the computer's receipt clock. A metrics-server clock may lag or lead this computer.
        now = fallback_time if self._clock is None else max(self._clock, fallback_time)
        self._clock = now
        cutoff = now - self.max_age_s
        for name in list(self.lines):
            points, receipts = self.lines[name], self._receipts[name]
            while receipts and receipts[0] <= cutoff:
                receipts.popleft()
                points.popleft()
            if not points:
                self._remove(name)
        added = 0
        for name, (t, cpu, mem) in sorted(sample.items()):
            t = t if t is not None else fallback_time
            points = self.lines.setdefault(name, deque(maxlen=self.max_points))
            receipts = self._receipts.setdefault(name, deque(maxlen=self.max_points))
            self._last_seen[name] = now
            if points and points[-1][0] >= t:
                continue
            points.append((t, cpu, mem))
            receipts.append(now)
            added += 1
        while len(self.lines) > self.max_series:
            # Prefer current lines and the total; then deterministically remove the least recently observed one.
            victim = min(self.lines, key=lambda name: (name == "total", name in sample, self._last_seen[name], name))
            self._remove(victim)
        return added

    def _remove(self, name):
        del self.lines[name]
        del self._receipts[name]
        del self._last_seen[name]

    def series(self, column):
        """{line: [(t, value)]} for `column` ("cpu" or "mem"), totals first."""
        index = 1 if column == "cpu" else 2
        names = sorted(self.lines, key=lambda n: (n != "total", n))
        return {n: [(p[0], p[index]) for p in self.lines[n]] for n in names}

    def count(self):
        return max((len(p) for p in self.lines.values()), default=0)

    def first_time(self):
        times = [p[0][0] for p in self.lines.values() if p]
        return min(times) if times else None

    def clear(self):
        self.lines = {}
        self._receipts, self._last_seen, self._clock = {}, {}, None


def reference_lines(kind, row):
    """Horizontal lines to draw for context: a pod's CPU and memory requests and limits, a node's allocatable amounts.
    {"cpu": {label: cores}, "mem": {label: MiB}}; zero or unset values are left out."""
    cpu, mem = {}, {}
    if kind == "Pod":
        for label, value in (("request", row.cpu_req), ("limit", row.cpu_lim)):
            if value > 0:
                cpu[label] = value
        for label, value in (("request", row.mem_req), ("limit", row.mem_lim)):
            if value > 0:
                mem[label] = value
    elif kind == "Node":
        if getattr(row, "cpu", 0) > 0:
            cpu["allocatable"] = row.cpu
        if getattr(row, "memory", 0) > 0:
            mem["allocatable"] = row.memory * 1024  # GiB -> MiB
    return {"cpu": cpu, "mem": mem}


def format_cores(value):
    return f"{value * 1000:.0f}m" if value < 1 else f"{value:.2f}"


def format_mib(value):
    return f"{value / 1024:.2f}Gi" if value >= 1024 else f"{value:.0f}Mi"
