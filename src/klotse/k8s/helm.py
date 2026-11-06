"""Helm 3 releases as the cluster stores them, and what Klotse shows of them. Pure functions, no cluster access, no Qt.

Helm 3 keeps each revision of a release as a Secret named `sh.helm.release.v1.<release>.v<revision>` (type
`helm.sh/release.v1`) in the release's namespace, labelled `owner=helm`, `name=<release>`, `status=<state>` and
`version=<revision>`. The release itself is in `data.release`: JSON, gzip-compressed, base64-encoded by Helm, and base64-encoded
again by Kubernetes, so one decode gives Helm's base64 text and a second gives the gzip. (The ConfigMap storage driver skips
Kubernetes' layer; `decode_release` copes with both.) The listing needs only the labels, so it never reads a Secret's data;
details and history read the data of the revisions of one release the user asked about, and values that look secret stay
hidden until the user reveals them. Nothing here logs a value."""
import base64
import binascii
import gzip
import io
import json
import re
from copy import deepcopy

LABEL_SELECTOR = "owner=helm"
SECRET_PREFIX = "sh.helm.release.v1."
MAX_RELEASE_BYTES = 64 * 1024 * 1024  # a release larger than this once decompressed is refused (a decompression bomb, or not Helm's)
GZIP_MAGIC = b"\x1f\x8b"
SECRETISH = re.compile(r"(?i)(pass(word|wd|phrase)?|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credential|auth|cert|\bkey$)")
HIDDEN = "<hidden>"


class HelmError(ValueError):
    """A release could not be read; the message says why."""


def secret_name(release, revision):
    return f"{SECRET_PREFIX}{release}.v{revision}"


def decode_release(value):
    """The release dict inside `value`: the `release` entry of a Helm storage Secret (base64 from Kubernetes) or ConfigMap.
    Raises HelmError for anything that is not a Helm release or is too large once decompressed."""
    try:
        data = base64.b64decode(value, validate=False)
        if not data.startswith(GZIP_MAGIC):
            data = base64.b64decode(data, validate=False)  # Helm's own base64 layer
        if data.startswith(GZIP_MAGIC):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
                data = handle.read(MAX_RELEASE_BYTES + 1)
            if len(data) > MAX_RELEASE_BYTES:
                raise HelmError("The release is too large to read.")
        release = json.loads(data)
    except (binascii.Error, OSError, EOFError, ValueError) as e:
        if isinstance(e, HelmError):
            raise
        raise HelmError("This is not a Helm release (cannot decode its data).") from e
    if not isinstance(release, dict) or "chart" not in release and "info" not in release:
        raise HelmError("This is not a Helm release (no chart or info).")
    return release


def _meta(release):
    return ((release.get("chart") or {}).get("metadata")) or {}


def summarize(release):
    """The facts of one revision, without its values or manifest: name, namespace, revision, status, chart, versions, dates,
    description and the release notes."""
    info = release.get("info") or {}
    meta = _meta(release)
    return {"name": release.get("name", ""), "namespace": release.get("namespace", ""), "revision": release.get("version", 0),
            "status": info.get("status", ""), "chart": meta.get("name", ""), "chart_version": meta.get("version", ""),
            "app_version": meta.get("appVersion", ""), "description": info.get("description", ""),
            "first_deployed": info.get("first_deployed", ""), "last_deployed": info.get("last_deployed", ""),
            "notes": info.get("notes", "")}


def deep_merge(base, over):
    """`over` laid on `base` the way Helm coalesces values: mappings merge, anything else replaces; None in `over` removes."""
    merged = deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (over or {}).items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def values_of(release):
    """(user-supplied values, all values): what was passed with --set / -f, and the chart's defaults with those on top."""
    user = release.get("config") or {}
    return user, deep_merge((release.get("chart") or {}).get("values") or {}, user)


def mask_values(node):
    """A copy of `node` with the value of every key that looks like a secret replaced by <hidden> (names containing password,
    secret, token, key, credential, auth, cert, ...). Whole sub-trees under such a key are hidden, not just their leaves."""
    if isinstance(node, dict):
        return {k: (HIDDEN if SECRETISH.search(str(k)) and v not in (None, "", {}, []) else mask_values(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [mask_values(v) for v in node]
    return node


VALID_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a release name: it goes into a label selector and a command line


def helm_uninstall_args(context, namespace, name, kubeconfig=None):
    """argv for `helm uninstall`, with an explicit --kube-context (and --kubeconfig), like every kubectl command here."""
    if not VALID_NAME.fullmatch(name or "") or not VALID_NAME.fullmatch(namespace or ""):
        raise HelmError(f"'{name}' in '{namespace}' is not a release name Helm would have created.")
    return (["helm"] + (["--kubeconfig", kubeconfig] if kubeconfig else []) +
            ["--kube-context", context, "uninstall", name, "--namespace", namespace])


def release_rows(items):
    """One row per release from the metadata of its storage Secrets (dicts with name, namespace, labels, creationTimestamp):
    the newest revision wins. [{name, namespace, revision, status, updated, revisions}], sorted by namespace and name. Items
    that are not Helm release Secrets are skipped."""
    newest, counts = {}, {}
    for item in items:
        labels = item.get("labels") or {}
        release = labels.get("name")
        try:
            revision = int(labels.get("version", ""))
        except ValueError:
            continue
        if labels.get("owner") != "helm" or not release:
            continue
        key = (item.get("namespace", ""), release)
        counts[key] = counts.get(key, 0) + 1
        if key not in newest or revision > newest[key]["revision"]:
            newest[key] = {"name": release, "namespace": key[0], "revision": revision, "status": labels.get("status", ""),
                           "updated": item.get("creationTimestamp", "")}
    return [dict(row, revisions=counts[key]) for key, row in sorted(newest.items())]
