"""A Prometheus or VictoriaMetrics source for the usage charts (pure, no Qt, no network): finding the service, building the queries, reading the
answers. Both speak the Prometheus HTTP API (`/api/v1/query_range`, PromQL; VictoriaMetrics' MetricsQL accepts these queries), so one reader
serves both. Klotse reaches the service through the **Kubernetes API server's service proxy** (`/api/v1/namespaces/NS/services/NAME:PORT/proxy/...`),
so no port-forward, address or extra login is needed: the user's own kubeconfig credentials do the work and need `get` on `services/proxy`.

Queries use the cAdvisor metrics every kubelet exposes (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`), which any
Prometheus that scrapes the kubelet has. A node is matched by the `instance` label (its name or internal IP), which is the setup of a
default kube-prometheus-stack; other relabelling can leave a node chart empty, and the window then says so. Not checked against a real
Prometheus or VictoriaMetrics: the answer shapes come from the documented API."""
import json
import re
from urllib.parse import quote, urlencode

RANGES = (("15 minutes", 900), ("1 hour", 3600), ("6 hours", 21600), ("24 hours", 86400), ("7 days", 604800))
DEFAULT_PORTS = {"prometheus": 9090, "victoria": 8428, "vmselect": 8481}
PREFIXES = {"vmselect": "/select/0/prometheus"}  # VictoriaMetrics cluster: the query path of tenant 0
NOT_A_SERVER = ("alertmanager", "exporter", "operator", "pushgateway", "adapter", "blackbox", "grafana", "kube-state", "vmagent", "vmalert",
                "vminsert", "vmstorage", "node-exporter", "webhook", "thanos")


def escape_re(text):
    """A string made safe to put inside a quoted PromQL regular expression (RE2): each special character is escaped for both layers."""
    return re.sub(r'([.+*?()|\[\]{}^$\\"])', lambda m: "\\\\" + m.group(1), str(text))


def quote_label(text):
    """A label value inside double quotes."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


# ---- finding the service ---------------------------------------------------------------------------------------------
def ref_text(ref):
    """`namespace/name:port` plus the path prefix when there is one."""
    return f"{ref['namespace']}/{ref['name']}:{ref['port']}{ref.get('prefix', '')}"


def parse_ref(text):
    """A service reference typed by the user: `monitoring/prometheus-server:80`, optionally followed by a path prefix such as
    `/select/0/prometheus` (VictoriaMetrics cluster) and optionally starting with `https:`. Returns the ref dict; ValueError says what is wrong."""
    match = re.fullmatch(r"\s*(?:(https?):)?([a-z0-9][a-z0-9.-]*)/([a-z0-9][a-z0-9.-]*):(\d{1,5})(/[^\s]*)?\s*", str(text), re.I)
    if not match:
        raise ValueError("Write it as namespace/service:port, for example monitoring/prometheus-server:80 "
                         "(a path prefix may follow, for example /select/0/prometheus)")
    scheme, namespace, name, port, prefix = match.groups()
    if not 0 < int(port) < 65536:
        raise ValueError("The port must be between 1 and 65535.")
    return {"namespace": namespace, "name": name, "port": int(port), "scheme": (scheme or "").lower(), "prefix": (prefix or "").rstrip("/")}


def _kind_of(name, labels):
    text = " ".join([name.lower()] + [str(v).lower() for k, v in (labels or {}).items() if k in ("app", "app.kubernetes.io/name", "app.kubernetes.io/component")])
    if any(bad in text for bad in NOT_A_SERVER):
        return None
    if "vmselect" in text:
        return "vmselect"
    if "vmsingle" in text or "victoria-metrics" in text or "victoriametrics" in text:
        return "victoria"
    if "prometheus" in text:
        return "prometheus"
    return None


def candidates(services):
    """Services (manifests) that look like a Prometheus or VictoriaMetrics query endpoint, best first: [{namespace, name, port, scheme,
    prefix, kind}]. Names and labels are looked at, not the cluster's conventions alone; the user can always type a service by hand."""
    found = []
    for service in services:
        meta = service.get("metadata") or {}
        name, namespace = meta.get("name", ""), meta.get("namespace", "")
        kind = _kind_of(name, meta.get("labels"))
        if kind is None:
            continue
        ports = [p for p in (service.get("spec") or {}).get("ports") or [] if p.get("port")]
        if not ports:
            continue
        wanted = DEFAULT_PORTS[kind]
        port = next((p for p in ports if p["port"] == wanted or p.get("name") in ("http", "web", "http-web", "http-server")), None) \
            or next((p for p in ports if p.get("protocol", "TCP") == "TCP"), ports[0])
        score = (3 if kind != "prometheus" or "server" in name or name.endswith("prometheus") else 1) + (2 if namespace in ("monitoring", "prometheus", "victoria-metrics", "observability") else 0)
        found.append((score, {"namespace": namespace, "name": name, "port": port["port"], "scheme": "https" if port.get("name") == "https" else "",
                              "prefix": PREFIXES.get(kind, ""), "kind": kind}))
    return [ref for _score, ref in sorted(found, key=lambda p: (-p[0], p[1]["namespace"], p[1]["name"]))]


def proxy_path(ref, api_path):
    """The API server path that reaches `api_path` (such as `/api/v1/query_range`) on the service."""
    target = f"{ref.get('scheme') + ':' if ref.get('scheme') else ''}{ref['name']}:{ref['port']}"
    return f"/api/v1/namespaces/{quote(ref['namespace'])}/services/{quote(target, safe=':')}/proxy{ref.get('prefix', '')}{api_path}"


def range_path(ref, promql, start, end, step):
    return proxy_path(ref, "/api/v1/query_range") + "?" + urlencode({"query": promql, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": f"{step:.0f}"})


def instant_path(ref, promql):
    return proxy_path(ref, "/api/v1/query") + "?" + urlencode({"query": promql})


def step_for(seconds):
    """A query step giving about 240 points, at least 15 s."""
    return max(15, int(seconds / 240))


# ---- queries --------------------------------------------------------------------------------------------------------
def _pod_filter(namespace, pods):
    names = [pods] if isinstance(pods, str) else list(pods)
    pod = f'pod="{quote_label(names[0])}"' if len(names) == 1 else f'pod=~"{"|".join(escape_re(n) for n in names)}"'
    return f'namespace="{quote_label(namespace)}",{pod},container!="",container!="POD"'


def queries(kind, namespace="", pods=(), node_names=(), window="5m"):
    """{"cpu": promql, "mem": promql} for a Pod (one pod name in `pods`), a workload (its current pod names) or a Node (`node_names`: its
    name and internal IP). CPU is in cores and memory in bytes, both split per container (a pod) or per pod (a workload) or not at all
    (a node). A node has two forms, `instance` first, then the `node` label (`queries(..., node_names=...)["cpu_alt"]`)."""
    if kind == "Node":
        by_instance = "|".join(f"{escape_re(n)}(:.*)?" for n in node_names if n)
        names = "|".join(escape_re(n) for n in node_names if n)
        return {"cpu": f'sum(rate(container_cpu_usage_seconds_total{{id="/",instance=~"{by_instance}"}}[{window}]))',
                "mem": f'sum(container_memory_working_set_bytes{{id="/",instance=~"{by_instance}"}})',
                "cpu_alt": f'sum(rate(container_cpu_usage_seconds_total{{id="/",node=~"{names}"}}[{window}]))',
                "mem_alt": f'sum(container_memory_working_set_bytes{{id="/",node=~"{names}"}})'}
    group = "container" if kind == "Pod" else "pod"
    selector = _pod_filter(namespace, pods)
    return {"cpu": f"sum by ({group}) (rate(container_cpu_usage_seconds_total{{{selector}}}[{window}]))",
            "mem": f"sum by ({group}) (container_memory_working_set_bytes{{{selector}}})"}


# ---- answers --------------------------------------------------------------------------------------------------------
class PrometheusError(Exception):
    """The source answered, but with an error (a bad query, a limit hit) or with something that is not the Prometheus API."""


def parse_matrix(text, label=None):
    """{line: [(epoch seconds, value)]} from a `query_range` answer. Lines are named by the `label` (container or pod) when given, else
    `total`. Raises PrometheusError for an error answer. NaN and missing samples are skipped."""
    try:
        body = json.loads(text)
    except ValueError:
        raise PrometheusError("The answer was not JSON: is this a Prometheus or VictoriaMetrics address?") from None
    if not isinstance(body, dict) or body.get("status") != "success":
        raise PrometheusError(str((body or {}).get("error") or "the source returned an error") if isinstance(body, dict) else "unexpected answer")
    result = (body.get("data") or {}).get("result") or []
    lines = {}
    for series in result:
        name = (series.get("metric") or {}).get(label, "") if label else ""
        points = []
        for stamp, value in series.get("values") or []:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number:  # not NaN
                points.append((float(stamp), number))
        if points:
            lines[name or "total"] = points
    return lines


def with_total(lines, limit=9):
    """Add a `total` line (the sum of the lines at each time) when there are several, and drop the separate lines when there are too many to
    chart (keeping the total)."""
    if len(lines) < 2 or "total" in lines:
        return lines
    sums = {}
    for points in lines.values():
        for t, v in points:
            sums[t] = sums.get(t, 0.0) + v
    total = sorted(sums.items())
    return {"total": total} if len(lines) > limit - 1 else {"total": total, **lines}


def to_chart(cpu_lines, mem_lines):
    """({line: [(t, cores)]}, {line: [(t, MiB)]}) ready for the chart (memory bytes become MiB)."""
    mem = {name: [(t, v / 2**20) for t, v in points] for name, points in mem_lines.items()}
    return dict(cpu_lines), mem
