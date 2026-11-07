"""Requests and limits against what pods use right now, grouped by workload (pure, no Qt, no network).

Input is a list of PodRow (requests/limits from the manifest, `cpu_used` in cores and `mem_used` in MiB from metrics-server, -1 =
unknown). Usage is **one sample**: metrics-server keeps no history, so a peak that is not happening now is missed. The advice is a
starting point to compare with real history (Prometheus, a dashboard), never a number to apply blindly."""
import math

OK, OVER, UNDER, NEAR, NO_REQUEST, NO_DATA = "ok", "over", "under", "near", "no-request", "no-data"
TEXT = {OK: "✔ Fine", OVER: "⚠ Over-provisioned", UNDER: "⚠ Uses more than requested", NEAR: "✖ Near its limit",
        NO_REQUEST: "⚠ No request set", NO_DATA: "No metrics"}  # symbol and words, never colour alone
ATTENTION = (NEAR, UNDER, NO_REQUEST, OVER)
HEADROOM = 1.25  # advice keeps 25% above what was seen
OVER_BELOW = 0.30  # used less than 30% of the request (and the advice saves a meaningful amount): over-provisioned
UNDER_ABOVE = 1.10  # used more than 110% of the request
NEAR_ABOVE = 0.90  # used more than 90% of the limit
CPU_STEP, MEM_STEP = 0.010, 16.0  # advice is rounded up to 10m of CPU and 16 MiB
CPU_FLOOR, MEM_FLOOR = 0.010, 32.0


def owner_of(pod):
    """(kind, name) of what runs a pod: its controller, with a ReplicaSet's hash cut off to name the Deployment; a bare pod is its own."""
    meta = (pod.raw or {}).get("metadata") or {}
    for ref in meta.get("ownerReferences") or []:
        if ref.get("controller"):
            kind, name = ref.get("kind", ""), ref.get("name", "")
            if kind == "ReplicaSet" and name.count("-") >= 1:
                return "Deployment", name.rsplit("-", 1)[0]
            if kind == "Job" and name.count("-") >= 1 and name.rsplit("-", 1)[1].isdigit():
                return "CronJob", name.rsplit("-", 1)[0]
            return kind, name
    return "Pod", pod.name


def round_up(value, step):
    return math.ceil(value / step - 1e-9) * step


def judge(request, limit, used, floor, step):
    """(verdict, suggested request) for one resource of one workload. `used` is the largest current usage among its pods."""
    if used < 0:
        return NO_DATA, None
    suggestion = max(floor, round_up(used * HEADROOM, step))
    if limit > 0 and used > limit * NEAR_ABOVE:
        return NEAR, suggestion
    if request <= 0:
        return NO_REQUEST, suggestion
    if used > request * UNDER_ABOVE:
        return UNDER, suggestion
    if used < request * OVER_BELOW and request - suggestion >= 2 * floor:  # a saving of 20m / 64Mi or more, not noise
        return OVER, suggestion
    return OK, None


def analyze(pods):
    """One dict per workload: kind, name, namespace, pods, per-pod cpu/mem request and limit (the largest among its pods), the
    largest usage, a verdict and a suggested request for each resource, and what the advice would free (cores, GiB; 0 when it
    would not free any). Sorted: workloads that need attention first, then by name."""
    groups = {}
    for pod in pods:
        if ((pod.raw or {}).get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        groups.setdefault((pod.namespace, *owner_of(pod)), []).append(pod)
    out = []
    for (namespace, kind, name), members in groups.items():
        row = {"namespace": namespace, "kind": kind, "name": name, "pods": len(members),
               "cpu_req": max(p.cpu_req for p in members), "cpu_lim": max(p.cpu_lim for p in members),
               "mem_req": max(p.mem_req for p in members), "mem_lim": max(p.mem_lim for p in members),
               "cpu_used": max((p.cpu_used for p in members), default=-1.0), "mem_used": max((p.mem_used for p in members), default=-1.0)}
        row["cpu_verdict"], row["cpu_suggest"] = judge(row["cpu_req"], row["cpu_lim"], row["cpu_used"], CPU_FLOOR, CPU_STEP)
        row["mem_verdict"], row["mem_suggest"] = judge(row["mem_req"], row["mem_lim"], row["mem_used"], MEM_FLOOR, MEM_STEP)
        row["cpu_free"] = max(0.0, (row["cpu_req"] - row["cpu_suggest"]) * len(members)) if row["cpu_verdict"] == OVER else 0.0
        row["mem_free"] = max(0.0, (row["mem_req"] - row["mem_suggest"]) * len(members) / 1024) if row["mem_verdict"] == OVER else 0.0
        out.append(row)
    return sorted(out, key=lambda r: (0 if needs_attention(r) else 1, r["namespace"], r["name"]))


def needs_attention(row):
    return row["cpu_verdict"] in ATTENTION or row["mem_verdict"] in ATTENTION


def cpu_text(cores):
    return "" if cores is None or cores < 0 else (f"{round(cores * 1000)}m" if cores < 1 else f"{cores:g}")


def mem_text(mib):
    if mib is None or mib < 0:
        return ""
    return f"{mib / 1024:.1f}Gi" if mib >= 1024 else f"{round(mib)}Mi"


def summary(rows):
    """One paragraph: how many workloads need attention and what the over-provisioned ones would free."""
    if not rows:
        return "No pods."
    if all(r["cpu_verdict"] == NO_DATA and r["mem_verdict"] == NO_DATA for r in rows):
        return "No usage figures: metrics-server did not answer, so only requests and limits can be shown."
    attention = sum(1 for r in rows if needs_attention(r))
    cpu, mem = sum(r["cpu_free"] for r in rows), sum(r["mem_free"] for r in rows)
    text = f"{attention} of {len(rows)} workloads need a look."
    if cpu > 0 or mem > 0:
        text += f" Following the advice for the over-provisioned ones would free about {cpu:.2f} cores and {mem:.1f} GiB of requests."
    return text + " Usage is one sample taken now: compare with real history before changing anything."
