"""Requests, limits and usage against allocatable amounts, per node and per namespace (pure, no Qt, no network). CPU is in
cores and memory in GiB; a usage of -1 means metrics-server did not answer. Pods that finished (Succeeded, Failed) hold nothing."""

FINISHED = ("Succeeded", "Failed")


def _live(pods):
    return [p for p in pods if ((getattr(p, "raw", None) or {}).get("status") or {}).get("phase") not in FINISHED]


def _used(values):
    """Sum of usages, -1 when none of them is known."""
    known = [v for v in values if v >= 0]
    return sum(known) if known else -1.0


def _sums(pods):
    return {"pods": len(pods), "cpu_req": sum(p.cpu_req for p in pods), "cpu_lim": sum(p.cpu_lim for p in pods),
            "mem_req": sum(p.mem_req for p in pods) / 1024, "mem_lim": sum(p.mem_lim for p in pods) / 1024,
            "cpu_used": _used([p.cpu_used for p in pods]),
            "mem_used": (lambda m: m / 1024 if m >= 0 else -1.0)(_used([p.mem_used for p in pods]))}


def percent(part, whole):
    """-1 when either is unknown or the whole is zero."""
    return part / whole * 100 if whole > 0 and part >= 0 else -1.0


def by_node(nodes, pods):
    """One dict per node: name, pods, cpu_alloc/req/lim/used and mem_* , cpu_req_pct, mem_req_pct. Pods not on a listed node are
    ignored here (they have none, or the node is gone)."""
    live = _live(pods)
    out = []
    for node in nodes:
        mine = [p for p in live if p.node == node.name]
        row = {"name": node.name, "cpu_alloc": node.cpu, "mem_alloc": node.memory, **_sums(mine)}
        # a node's own usage (metrics-server's node figure) is more complete than the sum over its pods
        row["cpu_used"] = node.cpu_used if node.cpu_used >= 0 else row["cpu_used"]
        row["mem_used"] = node.mem_used if node.mem_used >= 0 else row["mem_used"]
        row["cpu_req_pct"], row["mem_req_pct"] = percent(row["cpu_req"], node.cpu), percent(row["mem_req"], node.memory)
        out.append(row)
    return out


def by_namespace(pods):
    """One dict per namespace that has live pods: name, pods and the summed requests, limits and usage."""
    live = _live(pods)
    spaces = {}
    for p in live:
        spaces.setdefault(p.namespace, []).append(p)
    return [{"name": ns, **_sums(members)} for ns, members in sorted(spaces.items())]


def totals(node_rows):
    """The whole cluster's allocatable and requested amounts, from `by_node` rows."""
    cpu_alloc, mem_alloc = sum(r["cpu_alloc"] for r in node_rows), sum(r["mem_alloc"] for r in node_rows)
    cpu_req, mem_req = sum(r["cpu_req"] for r in node_rows), sum(r["mem_req"] for r in node_rows)
    return {"cpu_alloc": cpu_alloc, "mem_alloc": mem_alloc, "cpu_req": cpu_req, "mem_req": mem_req,
            "cpu_req_pct": percent(cpu_req, cpu_alloc), "mem_req_pct": percent(mem_req, mem_alloc)}
