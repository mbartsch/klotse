from datetime import datetime, timezone

from kubernetes import client


# Serialization is stateless; do not construct a connection pool for every row.
_serializer = client.ApiClient()


def format_age(ts, now=None):
    if ts is None:
        return ""
    secs = int(((now or datetime.now(timezone.utc)) - ts).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{max(secs, 0)}s"


def to_dict(obj):
    return _serializer.sanitize_for_serialization(obj)
