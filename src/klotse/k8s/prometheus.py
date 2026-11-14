"""A Prometheus or VictoriaMetrics source for the usage charts (pure, no Qt, no network): finding the service, building the queries, reading the
answers. Both speak the Prometheus HTTP API (`/api/v1/query_range`, PromQL; VictoriaMetrics' MetricsQL accepts these queries), so one reader
serves both. Klotse reaches the service through the **Kubernetes API server's service proxy** (`/api/v1/namespaces/NS/services/NAME:PORT/proxy/...`),
so no port-forward, address or extra login is needed: the user's own kubeconfig credentials do the work and need `get` on `services/proxy`.
When the proxy is not allowed, or the store is only reachable from this computer, the user can choose a **port-forward** instead: the workspace starts
`kubectl port-forward` and gives the ref a `local_port`; the query is then a plain HTTP GET to 127.0.0.1 (`KubeService._prometheus_get`).

Queries use the cAdvisor metrics every kubelet exposes (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`), which any
Prometheus that scrapes the kubelet has. A node is matched by the `instance` label (its name or internal IP), which is the setup of a
default kube-prometheus-stack; other relabelling can leave a node chart empty, and the window then says so. Not checked against a real
Prometheus or VictoriaMetrics: the answer shapes come from the documented API."""
import json
import re
from urllib.parse import quote, urlencode, urlsplit

AUTH_KINDS = ("", "bearer", "basic")  # URL sources only: no authentication, a bearer token, or a user name and password

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
    """`namespace/name:port` plus the path prefix when there is one; the URL itself for a URL source."""
    if ref.get("url"):
        return ref["url"]
    return f"{ref['namespace']}/{ref['name']}:{ref['port']}{ref.get('prefix', '')}"


def parse_ref(text):
    """A service reference typed by the user: `monitoring/prometheus-server:80`, optionally followed by a path prefix such as
    `/select/0/prometheus` (VictoriaMetrics cluster) and optionally starting with `https:`. Returns the ref dict; ValueError says what is wrong."""
    if re.match(r"\s*https?://", str(text), re.I):
        return parse_url(text)
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


def parse_url(text):
    """A URL source typed as `https://host[:port][/path prefix]`, for example https://vm.example.com/select/0/prometheus. It is called
    directly from this computer (not through the cluster). No user name or password in the URL: the auth option keeps those out of the
    settings file. ValueError says what is wrong."""
    parts = urlsplit(str(text).strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("A URL must be http:// or https:// with a host name, for example https://vm.example.com/select/0/prometheus")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError("Leave the user name and password out of the URL: choose the authentication option instead.")
    if parts.query or parts.fragment:
        raise ValueError("Leave out the query and the fragment: only the host and an optional path prefix are needed.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as error:
        raise ValueError("The port must be a number between 1 and 65535.") from error
    prefix = parts.path.rstrip("/")
    origin = f"{parts.scheme}://{parts.netloc}"
    return {"url": f"{origin}{prefix}", "origin": origin, "namespace": "", "name": parts.hostname, "port": port,
            "scheme": parts.scheme, "prefix": prefix}


def with_auth(ref, auth="", auth_env="", auth_user=""):
    """`ref` with the authentication of a URL source: `auth` is one of AUTH_KINDS, `auth_env` the environment variable that holds the
    token (bearer) or the password (basic), `auth_user` the user name for basic. The secret itself is never stored here."""
    if not ref.get("url") or auth not in AUTH_KINDS or not auth:
        return ref
    return {**ref, "auth": auth, "auth_env": str(auth_env or "").strip(), "auth_user": str(auth_user or "").strip()}


def proxy_path(ref, api_path):
    """The API server path that reaches `api_path` (such as `/api/v1/query_range`) on the service."""
    target = f"{ref.get('scheme') + ':' if ref.get('scheme') else ''}{ref['name']}:{ref['port']}"
    return f"/api/v1/namespaces/{quote(ref['namespace'])}/services/{quote(target, safe=':')}/proxy{ref.get('prefix', '')}{api_path}"


def reach_path(ref, api_path):
    """Where `api_path` is asked for: the API server proxy path, or, when `ref` carries `local_port` (a port-forward Klotse started), the
    path on that local address (the service's own path prefix, no proxy)."""
    if ref.get("url"):
        return f"{ref.get('prefix', '')}{api_path}"  # a URL source is called directly: the path prefix is part of its URL
    if ref.get("local_port"):
        return f"{ref.get('prefix', '')}{api_path}"
    return proxy_path(ref, api_path)


def range_path(ref, promql, start, end, step):
    return reach_path(ref, "/api/v1/query_range") + "?" + urlencode({"query": promql, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": f"{step:.0f}"})


def instant_path(ref, promql):
    return reach_path(ref, "/api/v1/query") + "?" + urlencode({"query": promql})


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


METRIC_NAMES_LIMIT = 5000  # most metric names the query window keeps for autocomplete


def metric_names_path(ref):
    """The API path of every metric name the source knows (`/api/v1/label/__name__/values`), through the proxy or a forward."""
    return reach_path(ref, "/api/v1/label/__name__/values")


def parse_names(text, limit=METRIC_NAMES_LIMIT):
    """Sorted metric names from a label-values answer, at most `limit`. Raises PrometheusError for an error answer."""
    try:
        body = json.loads(text)
    except ValueError:
        raise PrometheusError("The answer was not JSON: is this a Prometheus or VictoriaMetrics address?") from None
    if not isinstance(body, dict) or body.get("status") != "success":
        raise PrometheusError(str((body or {}).get("error") or "the source returned an error") if isinstance(body, dict) else "unexpected answer")
    names = sorted({str(name) for name in (body.get("data") or []) if isinstance(name, str) and name})
    return names[:limit]


def parse_vector(text):
    """[(labels, value, time)] from an instant `query` answer (`vector` results): every label of the series as the source gives it, the
    latest value and the epoch time of that sample. Series without a number are skipped. Raises PrometheusError for an error answer."""
    try:
        body = json.loads(text)
    except ValueError:
        raise PrometheusError("The answer was not JSON: is this a Prometheus or VictoriaMetrics address?") from None
    if not isinstance(body, dict) or body.get("status") != "success":
        raise PrometheusError(str((body or {}).get("error") or "the source returned an error") if isinstance(body, dict) else "unexpected answer")
    found = []
    for series in (body.get("data") or {}).get("result") or []:
        sample = series.get("value") if isinstance(series, dict) else None
        if not isinstance(sample, list) or len(sample) != 2:
            continue
        try:
            number, stamp = float(sample[1]), float(sample[0])
        except (TypeError, ValueError):
            continue
        if number == number:  # not NaN
            found.append((dict(series.get("metric") or {}), number, stamp))
    return found


def parse_matrix_series(text):
    """{series text: [(epoch seconds, value)]} from a `query_range` answer, one line per series (named by its labels; see `series_text`).
    Raises PrometheusError for an error answer. NaN and missing samples are skipped."""
    try:
        body = json.loads(text)
    except ValueError:
        raise PrometheusError("The answer was not JSON: is this a Prometheus or VictoriaMetrics address?") from None
    if not isinstance(body, dict) or body.get("status") != "success":
        raise PrometheusError(str((body or {}).get("error") or "the source returned an error") if isinstance(body, dict) else "unexpected answer")
    lines = {}
    for series in (body.get("data") or {}).get("result") or []:
        points = []
        for stamp, value in series.get("values") or []:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number:
                points.append((float(stamp), number))
        if points:
            lines[series_text(series.get("metric") or {})] = points
    return lines


def series_text(labels):
    """`__name__="x", pod="web-1", container="app"`: every label of a series, in the order the source gives them (nothing dropped or sorted)."""
    return ", ".join(f'{k}="{v}"' for k, v in labels.items()) or "(no labels)"


def workload_queries(kind, namespace, name):
    """Quick PromQL for a workload: [(title, query)]. Pod CPU, memory and network come from the kubelet's cAdvisor metrics; restarts and
    replica counts need kube-state-metrics (the title says so). Pod names follow the controller's naming: Deployment pods are
    `name-<hash>-<hash>`, StatefulSet `name-<n>`, DaemonSet `name-<hash>`."""
    if kind == "Node":  # a node has no namespace: its CPU and memory are the root cgroup's, from the instance or node label
        node = queries("Node", node_names=[name])
        return [("CPU, cores", node["cpu"]), ("Memory, MiB", f"({node['mem']})/1048576")]
    ns, nm = quote_label(namespace), quote_label(name)
    if kind == "Pod":  # one pod: its name is exact
        pods = f'pod="{nm}"'
        pattern = None
    else:
        pattern = {"Deployment": r"[a-z0-9]+-[a-z0-9]+", "StatefulSet": r"[0-9]+", "DaemonSet": r"[a-z0-9]+"}.get(kind, r"[a-z0-9]+")
        pods = f'pod=~"{escape_re(name)}-{pattern}"'
    replicas = {"Deployment": ("Available replicas (kube-state-metrics)", f'kube_deployment_status_replicas_available{{namespace="{ns}",deployment="{nm}"}}'),
                "StatefulSet": ("Ready replicas (kube-state-metrics)", f'kube_statefulset_status_replicas_ready{{namespace="{ns}",statefulset="{nm}"}}'),
                "DaemonSet": ("Ready pods (kube-state-metrics)", f'kube_daemonset_status_number_ready{{namespace="{ns}",daemonset="{nm}"}}')}
    presets = [
        ("CPU, cores", f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",{pods},container!="",container!="POD"}}[5m]))'),
        ("Memory, MiB", f'sum(container_memory_working_set_bytes{{namespace="{ns}",{pods},container!="",container!="POD"}})/1048576'),
        ("Network in, bytes/s", f'sum(rate(container_network_receive_bytes_total{{namespace="{ns}",{pods}}}[5m]))'),
        ("Network out, bytes/s", f'sum(rate(container_network_transmit_bytes_total{{namespace="{ns}",{pods}}}[5m]))'),
        ("Restarts in the last hour (kube-state-metrics)",
         f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}",{pods}}}[1h]))'),
    ]
    if kind in replicas:
        presets.insert(0, replicas[kind])
    return presets
