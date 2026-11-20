"""Teleport Kubernetes clusters for Add clusters from the cloud: list them with `tsh kube ls` and write one cluster's
kubeconfig entry with `tsh kube login` (no Qt).

Klotse never signs in to Teleport: the user runs `tsh login --proxy=...` in a terminal first (SSO and MFA need a browser or a
prompt that a command without a terminal cannot show). `tsh kube login` writes into the file named by KUBECONFIG; core gives
it a private temporary file and merges that afterwards, so the user's kubeconfig and its current-context are never touched
by tsh (its switch of current-context only changes the temporary file, which the merge does not copy over the target's).

The JSON field names of `tsh kube ls --format=json` (`kube_cluster_name`, `labels`, `selected`) have not been checked
against a real tsh; the parser also accepts `name` and `metadata.name`/`metadata.labels` and a list wrapped in an object."""
import json
import re

from klotse.plugins.api import CloudError, ClusterField, ClusterRef, ClusterSource

SOURCE = ClusterSource("teleport", "Teleport", (ClusterField("proxy", "Teleport proxy (optional, host:port)"),
                                                ClusterField("tsh", "tsh command (empty: tsh on the PATH)")))
PROXY = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}(?::\d{1,5})?")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
TSH_NAMES = ("tsh", "tsh.exe")
NOT_INSTALLED = ("tsh (Teleport's command-line client) is not installed or not on the PATH. Install it, or type the full path "
                 "of tsh in the tsh command field.")
NOT_SIGNED_IN = re.compile(r"not logged in|log ?in again|please (?:re)?log ?in|session (?:has )?expired|expired|"
                           r"no (?:active )?profile|tsh login|reauthenticat", re.IGNORECASE)


def untrusted(value):
    """True when a value from Teleport (or typed) must not reach a command line: empty, an option, control characters."""
    return not value or value.startswith("-") or bool(CONTROL.search(value)) or len(value) > 253


def tsh_command(value):
    """The tsh executable to run: "tsh" when empty, else the typed path, which must name tsh (or tsh.exe)."""
    value = str(value or "").strip()
    if not value:
        return "tsh"
    base = re.split(r"[\\/]", value)[-1].lower()
    if untrusted(value) or base not in TSH_NAMES:
        raise CloudError("The tsh command must be tsh or the full path of tsh (tsh.exe on Windows).")
    return value


def clean_scope(scope):
    proxy = str(scope.get("proxy") or "").strip()
    if proxy and not PROXY.fullmatch(proxy):
        raise CloudError(f"Teleport proxy must be host or host:port, without https:// ({proxy[:40]!r} is not)")
    return {"proxy": proxy, "tsh": tsh_command(scope.get("tsh"))}


def plain_error(error, proxy):
    """The usual tsh failures in plain words; other failures keep tsh's own short reason."""
    text = str(error)
    if "is not installed" in text or text.startswith("could not run"):
        return NOT_INSTALLED
    if NOT_SIGNED_IN.search(text):
        login = f"tsh login --proxy={proxy}" if proxy else "tsh login --proxy=<your Teleport proxy>"
        return (f"Not signed in to Teleport, or the session expired. Run `{login}` in a terminal first, then try again "
                "(Klotse runs tsh without a terminal, so it cannot show a browser sign-in or an MFA prompt).")
    return text


def tsh(run, argv, proxy, env=None):
    try:
        return run(argv, env=env)
    except CloudError as error:
        raise CloudError(plain_error(error, proxy)) from None


def _items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("kube_clusters", "clusters", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    raise CloudError("tsh answered with an unexpected cluster list")


def _labels(item):
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    found = {}
    for labels in (item.get("labels"), item.get("static_labels"), metadata.get("labels")):
        if isinstance(labels, dict):
            for key, value in labels.items():
                if isinstance(value, dict):  # dynamic labels: {"result": ...}
                    value = value.get("result", "")
                found.setdefault(str(key), str(value))
    return ", ".join(f"{key}={value}" for key, value in sorted(found.items()) if not CONTROL.search(key + value))[:200]


def parse(text, proxy):
    """[ClusterRef] from `tsh kube ls --format=json`. Names that are empty, start with "-" or have control characters are
    left out. CloudError when Teleport lists no cluster this user may reach."""
    text = text or ""
    try:
        data = json.loads(text or "null")
    except ValueError:
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)  # a warning line before the JSON
        try:
            data = json.loads(text[start:]) if start >= 0 else None
        except ValueError:
            data = None
        if data is None:
            raise CloudError("tsh answered with something that is not JSON") from None
    if data is None:
        data = []
    clusters, seen, refused, total = [], set(), 0, 0
    for item in _items(data):
        if not isinstance(item, dict):
            continue
        total += 1
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = next((v for v in (item.get("kube_cluster_name"), item.get("name"), metadata.get("name")) if isinstance(v, str) and v), "")
        if untrusted(name):
            refused += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        where = next((v for v in (item.get("cluster"), item.get("teleport_cluster")) if isinstance(v, str) and v), "") or proxy
        clusters.append(ClusterRef(name, name, "" if untrusted(where) else where, group=_labels(item),
                                   state="selected" if item.get("selected") is True else ""))
    if not clusters:
        if refused:
            raise CloudError(f"Teleport listed {refused} cluster(s), but none has a name Klotse accepts "
                             "(empty, starting with '-' or with control characters).")
        raise CloudError("Teleport lists no Kubernetes clusters that this user may reach.")
    return clusters


def list_clusters(scope, run):
    s = clean_scope(scope)
    argv = [s["tsh"]] + ([f"--proxy={s['proxy']}"] if s["proxy"] else []) + ["kube", "ls", "--format=json"]
    return parse(tsh(run, argv, s["proxy"]), s["proxy"])


def cluster_credentials(cluster, scope, target, run):
    """`tsh kube login <name>` with KUBECONFIG set to `target`, the private file core merges afterwards."""
    s = clean_scope(scope)
    name = str(cluster.ref or "")
    if untrusted(name):
        raise CloudError("The cluster name from Teleport is not accepted (empty, starting with '-' or with control characters).")
    argv = [s["tsh"]] + ([f"--proxy={s['proxy']}"] if s["proxy"] else []) + ["kube", "login", name]
    tsh(run, argv, s["proxy"], env={"KUBECONFIG": str(target)})
