"""Readable (HTML) rendering of RBAC objects for the Details panel. Pure functions on the manifest dict, no Qt.

Everything that comes from the cluster is escaped: names, labels and rules are untrusted text."""
from html import escape

from .theme import ACCENT, BORDER, MUTED

RBAC_KINDS = ("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "ServiceAccount")
WARN = "#f5a524"
SENSITIVE = {"secrets", "*"}  # resources worth a second look
WRITE_VERBS = {"create", "update", "patch", "delete", "deletecollection", "escalate", "bind", "impersonate", "*"}


def _e(value):
    return escape(str(value), quote=True)


def _list(values, empty="–", warn=None):
    """Comma separated cells; items for which warn(item) is true are highlighted."""
    if not values:
        return f"<span style='color:{MUTED}'>{empty}</span>"
    parts = []
    for v in values:
        text = _e(v if v != "" else "(core)")
        parts.append(f"<b style='color:{WARN}'>{text}</b>" if warn and warn(v) else text)
    return ", ".join(parts)


def _table(headers, rows):
    head = "".join(f"<th align='left' style='color:{MUTED}; padding:3px 12px 3px 0'>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td valign='top' style='padding:3px 12px 3px 0'>{c}</td>" for c in row) + "</tr>"
                   for row in rows)
    return f"<table cellspacing='0' width='100%'><tr>{head}</tr>{body}</table>"


def _section(title, inner):
    return (f"<h3 style='color:{ACCENT}; margin:14px 0 4px 0'>{_e(title)}</h3>{inner}")


def _pairs(mapping):
    if not mapping:
        return f"<span style='color:{MUTED}'>none</span>"
    return "<br>".join(f"<span style='color:{MUTED}'>{_e(k)}</span> = {_e(v)}" for k, v in sorted(mapping.items()))


def _header(kind, raw):
    meta = raw.get("metadata") or {}
    lines = [f"<h2 style='margin:0'>{_e(kind)} <span style='color:{ACCENT}'>{_e(meta.get('name', ''))}</span></h2>"]
    facts = []
    if meta.get("namespace"):
        facts.append(("Namespace", meta["namespace"]))
    if meta.get("creationTimestamp"):
        facts.append(("Created", meta["creationTimestamp"]))
    if meta.get("uid"):
        facts.append(("UID", meta["uid"]))
    if facts:
        lines.append("<p style='margin:4px 0'>" + " &nbsp;·&nbsp; ".join(
            f"<span style='color:{MUTED}'>{_e(k)}</span> {_e(v)}" for k, v in facts) + "</p>")
    return "".join(lines)


def _metadata(raw):
    meta = raw.get("metadata") or {}
    return (_section("Labels", _pairs(meta.get("labels"))) +
            _section("Annotations", _pairs({k: v for k, v in (meta.get("annotations") or {}).items()
                                            if k != "kubectl.kubernetes.io/last-applied-configuration"})))


def _rules(raw):
    rules = raw.get("rules") or []
    rows = []
    for r in rules:
        resources = r.get("resources") or []
        verbs = r.get("verbs") or []
        urls = r.get("nonResourceURLs") or []
        rows.append((
            _list(r.get("apiGroups") or ([] if urls else [""]), warn=lambda g: g == "*"),
            _list(resources or urls, warn=lambda x: x in SENSITIVE),
            _list(r.get("resourceNames")),
            _list(verbs, warn=lambda v: v in WRITE_VERBS)))
    if not rows:
        return _section("Rules", f"<span style='color:{MUTED}'>No rules: this grants nothing by itself.</span>")
    note = (f"<p style='color:{MUTED}; margin:2px 0'>{len(rules)} rule(s). "
            f"<b style='color:{WARN}'>Highlighted</b>: wildcards, secrets and verbs that change things.</p>")
    return _section("Rules", note + _table(("API groups", "Resources / URLs", "Resource names", "Verbs"), rows))


def _aggregation(raw):
    selectors = ((raw.get("aggregationRule") or {}).get("clusterRoleSelectors")) or []
    if not selectors:
        return ""
    items = []
    for s in selectors:
        labels = ", ".join(f"{_e(k)}={_e(v)}" for k, v in (s.get("matchLabels") or {}).items())
        exprs = ", ".join(f"{_e(e.get('key'))} {_e(e.get('operator'))} [{_e(','.join(e.get('values') or []))}]"
                          for e in s.get("matchExpressions") or [])
        items.append(", ".join(x for x in (labels, exprs) if x) or "(matches nothing)")
    return _section("Aggregated from ClusterRoles labelled", "<br>".join(items) +
                    f"<p style='color:{MUTED}; margin:4px 0'>The rules below are filled in by the controller.</p>")


def _binding(raw):
    ref = raw.get("roleRef") or {}
    role = (f"{_e(ref.get('kind', ''))} <b>{_e(ref.get('name', ''))}</b> "
            f"<span style='color:{MUTED}'>({_e(ref.get('apiGroup', ''))})</span>")
    subjects = raw.get("subjects") or []
    rows = [(_e(s.get("kind", "")), f"<b>{_e(s.get('name', ''))}</b>", _e(s.get("namespace") or "–"),
             _e(s.get("apiGroup") or "–")) for s in subjects]
    subj = _table(("Kind", "Name", "Namespace", "API group"), rows) if rows else (
        f"<span style='color:{MUTED}'>No subjects: nobody gets this role through this binding.</span>")
    return _section("Grants role", role) + _section(f"To {len(subjects)} subject(s)", subj)


def _serviceaccount(raw):
    secrets = [s.get("name", "") for s in raw.get("secrets") or []]
    pulls = [s.get("name", "") for s in raw.get("imagePullSecrets") or []]
    automount = raw.get("automountServiceAccountToken")
    return (_section("Token automount", _e("not set (default: on)" if automount is None else automount)) +
            _section("Secrets", _list(secrets, "none")) + _section("Image pull secrets", _list(pulls, "none")))


def render(kind, raw):
    """HTML for `kind` (one of RBAC_KINDS) built from its manifest dict."""
    if kind not in RBAC_KINDS or not isinstance(raw, dict):
        raise ValueError(f"No formatted view for {kind}")
    body = _header(kind, raw)
    if kind in ("Role", "ClusterRole"):
        body += _aggregation(raw) + _rules(raw)
    elif kind in ("RoleBinding", "ClusterRoleBinding"):
        body += _binding(raw)
    else:
        body += _serviceaccount(raw)
    return (f"<div style='font-size:13px'>{body}{_metadata(raw)}"
            f"<hr style='border-color:{BORDER}'></div>")
