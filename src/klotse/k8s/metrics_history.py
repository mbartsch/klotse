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


class History:
    """Points per line: `{line: deque of (t, cpu, mem)}`. A point with the same time as the line's last one is the same sample."""

    def __init__(self, max_points=MAX_POINTS):
        self.max_points, self.lines = max_points, {}

    def add(self, sample, fallback_time):
        """Store `sample` ({line: (t, cpu, mem)}); `fallback_time` stands in for a sample without a timestamp. Returns how many lines
        got a new point."""
        added = 0
        for name, (t, cpu, mem) in sample.items():
            t = t if t is not None else fallback_time
            points = self.lines.setdefault(name, deque(maxlen=self.max_points))
            if points and points[-1][0] >= t:
                continue
            points.append((t, cpu, mem))
            added += 1
        return added

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
