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


MORE_SCHEME = "klotse-more:"


def more_link(relation, kind, depth):
    """The href of a '... and N more' note: it toggles showing every entry of that group."""
    return MORE_SCHEME + quote(json.dumps([relation, kind, depth]))


def parse_more(href):
    """(relation, kind, depth) from a link made by more_link(), or None for any other link."""
    text = str(href)
    if not text.startswith(MORE_SCHEME):
        return None
    try:
        relation, kind, depth = json.loads(unquote(text[len(MORE_SCHEME):]))
    except (ValueError, TypeError):
        return None
    return (relation, kind, depth) if isinstance(relation, str) and isinstance(kind, str) and isinstance(depth, int) else None


def render(relations, name="", expanded=frozenset()):
    """HTML for the relations of one object, grouped by relation in reading order. A capped group ends with a link that shows
    every entry of it (`expanded` holds the groups currently shown in full)."""
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
            if r.note == "more":
                key = (relation, r.kind, r.depth)
                if key not in expanded:
                    indent = "&nbsp;" * 4 * r.depth + ("↳&nbsp;" if r.depth else "")
                    rows.append(f"<tr><td>{indent}<a href='{more_link(relation, r.kind, r.depth)}' style='color:{theme.MUTED}'>"
                                f"{_e(r.name)} (show all)</a></td></tr>")
                    continue
                for entry in r.more:
                    rows.append(_entry_row(entry))
                rows.append(f"<tr><td>{'&nbsp;' * 4 * r.depth}<a href='{more_link(relation, r.kind, r.depth)}'>show fewer</a></td></tr>")
                continue
            if r.note == "partial":
                indent = "&nbsp;" * 4 * r.depth + ("↳&nbsp;" if r.depth else "")
                rows.append(f"<tr><td style='color:{theme.MUTED}'>{indent}{_e(r.name)}</td><td></td></tr>")
                continue
            rows.append(_entry_row(r))
        parts.append(f"<h3 style='color:{theme.ACCENT_HI}; margin:12px 0 4px 0'>{_e(SECTION_TITLES.get(relation, relation))}</h3>"
                     f"<table cellspacing='0'>{''.join(rows)}</table>")
    return "".join(parts)


def _entry_row(r):
    indent = "&nbsp;" * 4 * r.depth + ("↳&nbsp;" if r.depth else "")
    where = f" <span style='color:{theme.MUTED}'>{_e(r.namespace)}</span>" if r.namespace else ""
    tag = f" <span style='color:{theme.MUTED}'>({_e(r.note)})</span>" if r.note else ""
    return (f"<tr><td>{indent}<span style='color:{theme.MUTED}'>{_e(r.kind)}</span> "
            f"<a href='{link(r.kind, r.namespace, r.name)}'>{_e(r.name)}</a>{where}{tag}</td></tr>")
