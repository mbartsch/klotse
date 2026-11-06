"""The Related page of the Details panel: owners, what an object owns, uses and is used by, each a link to jump there. Pure
functions (no Qt) on the Relations `k8s.related` returns; every string from the cluster is escaped, and a link carries its
target as JSON so a name can never be mistaken for markup or a command."""
import json
from urllib.parse import quote, unquote

from . import theme
from .rbac_view import _e

SCHEME = "klotse-goto:"
ORDER = ("Owned by", "Runs on", "Uses", "Selected by", "Owns", "Runs", "Selects", "Backed by", "Routes to", "Bound to", "Used by")
SECTION_TITLES = {"Owned by": "Owned by", "Runs on": "Runs on", "Uses": "Uses", "Selected by": "Selected by", "Owns": "Owns",
                  "Runs": "Pods on this node", "Selects": "Pods it selects", "Backed by": "Backed by", "Routes to": "Routes to",
                  "Bound to": "Bound to", "Used by": "Used by"}


def link(kind, namespace, name):
    """The href that stands for 'go to this object'."""
    return SCHEME + quote(json.dumps([kind, namespace, name]))


def parse_link(href):
    """(kind, namespace, name) from an href made by link(), or None for any other link."""
    text = str(href)
    if not text.startswith(SCHEME):
        return None
    try:
        kind, namespace, name = json.loads(unquote(text[len(SCHEME):]))
    except (ValueError, TypeError):
        return None
    return (kind, namespace, name) if all(isinstance(v, str) for v in (kind, namespace, name)) else None


def render(relations, name=""):
    """HTML for the relations of one object, grouped by relation in reading order."""
    if not relations:
        return (f"<p style='color:{theme.MUTED}'>No related objects found for {_e(name) or 'this object'}. Either it has none, or "
                "they could not be read (no permission, or already gone).</p>")
    groups = {}
    for r in relations:
        groups.setdefault(r.relation, []).append(r)
    parts = []
    for relation in [o for o in ORDER if o in groups] + [o for o in groups if o not in ORDER]:
        rows = []
        for r in groups[relation]:
            indent = "&nbsp;" * 4 * r.depth + ("↳&nbsp;" if r.depth else "")
            if r.note in ("more", "partial"):
                rows.append(f"<tr><td style='color:{theme.MUTED}'>{indent}{_e(r.name)}</td><td></td></tr>")
                continue
            where = f" <span style='color:{theme.MUTED}'>{_e(r.namespace)}</span>" if r.namespace else ""
            tag = f" <span style='color:{theme.MUTED}'>({_e(r.note)})</span>" if r.note else ""
            rows.append(f"<tr><td>{indent}<span style='color:{theme.MUTED}'>{_e(r.kind)}</span> "
                        f"<a href='{link(r.kind, r.namespace, r.name)}'>{_e(r.name)}</a>{where}{tag}</td></tr>")
        parts.append(f"<h3 style='color:{theme.ACCENT_HI}; margin:12px 0 4px 0'>{_e(SECTION_TITLES.get(relation, relation))}</h3>"
                     f"<table cellspacing='0'>{''.join(rows)}</table>")
    return "".join(parts)
