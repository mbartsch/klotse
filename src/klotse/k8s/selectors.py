"""Label selectors as the text the API wants in `labelSelector` (pure, no Qt, no network)."""
import re

_KEY = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
_VALUE = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")


def selector_text(selector):
    """`matchLabels` and `matchExpressions` of a workload (or a Service's plain label map) as `a=b,c in (x,y),!d`; '' for no
    selector (such an object has no pods this way, and an empty selector would match every pod, so callers must check)."""
    if not isinstance(selector, dict) or not selector:
        return ""
    if "matchLabels" not in selector and "matchExpressions" not in selector:  # a Service: {"app": "web"}
        selector = {"matchLabels": selector}
    parts = [f"{k}={v}" for k, v in sorted((selector.get("matchLabels") or {}).items())]
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr.get("key", ""), expr.get("operator", ""), ",".join(expr.get("values") or [])
        if op == "In":
            parts.append(f"{key} in ({values})")
        elif op == "NotIn":
            parts.append(f"{key} notin ({values})")
        elif op == "Exists":
            parts.append(key)
        elif op == "DoesNotExist":
            parts.append(f"!{key}")
    return ",".join(parts)


def valid_selector(text):
    """None when `text` looks like a usable selector, else a message. Checks the simple forms (`a=b`, `a!=b`, `a==b`, `a`, `!a`,
    `a in (x,y)`, `a notin (x)`) well enough to catch typos before a request is made; the server has the last word."""
    text = text.strip()
    if not text:
        return "Type a label selector, for example app=web,tier=frontend."
    for part in re.split(r",(?![^()]*\))", text):  # commas inside parentheses belong to in/notin
        part = part.strip()
        found = re.fullmatch(r"(\S+)\s+(in|notin)\s+\(([^()]*)\)", part)
        if found:
            key, values = found.group(1), [v.strip() for v in found.group(3).split(",")]
            if not _KEY.match(key) or not all(_VALUE.match(v) for v in values):
                return f"Not a valid selector: {part}"
            continue
        found = re.fullmatch(r"([^=!\s]+)\s*(==|=|!=)\s*(\S*)", part)
        if found:
            if not _KEY.match(found.group(1)) or not _VALUE.match(found.group(3)):
                return f"Not a valid selector: {part}"
            continue
        if not _KEY.match(part.lstrip("!")):
            return f"Not a valid selector: {part}"
    return None
