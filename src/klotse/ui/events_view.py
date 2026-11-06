"""The Events page of the Details panel: what the cluster says happened to one object, newest first. Pure functions
(no Qt) on the event rows `KubeService.list_object_events` returns. Every string from the cluster is escaped."""
from . import theme
from .rbac_view import _e, _table
from .workload_view import badge

SHOWN = 200  # the service never returns more; the page says when it is full


def _source(raw):
    raw = raw if isinstance(raw, dict) else {}
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return source.get("component") or raw.get("reportingComponent") or ""


def _type(event):
    """Warning is marked with a symbol and the word as well as the colour; anything else (Normal) is plain."""
    kind = str(getattr(event, "type", "") or "Normal")
    return badge(kind, "warn") if kind == "Warning" else _e(kind)


def render(events, name=""):
    """HTML for the events of one object. `events` are rows with type, reason, message, count and last_seen."""
    if not events:
        return (f"<p style='color:{theme.MUTED}'>No events for {_e(name) or 'this object'}. The cluster keeps events for "
                "about an hour, so older ones are gone.</p>")
    rows = []
    for e in events:
        count = getattr(e, "count", 1) or 1
        rows.append([_type(e), _e(getattr(e, "reason", "")), _e(getattr(e, "last_seen", "")) + (f" (×{count})" if count > 1 else ""),
                     _e(_source(getattr(e, "raw", None))), _e(getattr(e, "message", ""))])
    warnings = sum(1 for e in events if getattr(e, "type", "") == "Warning")
    note = f"{len(events)} event{'s' if len(events) != 1 else ''}, newest first" + (f" · {warnings} warning{'s' if warnings != 1 else ''}"
                                                                                   if warnings else "")
    if len(events) >= SHOWN:
        note += f" · showing the newest {SHOWN}"
    return (f"<p style='color:{theme.MUTED}; margin:2px 0 6px 0'>{_e(note)}</p>"
            + _table(["Type", "Reason", "Age", "From", "Message"], rows))
