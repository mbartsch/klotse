"""Words for the change log: what a mutating call did, without any object body, Secret data or patch contents (pure)."""


def describe(value):
    """A short text for one argument of a mutating call: scalars as they are, a manifest dict by kind/namespace/name only,
    anything else (patch bodies, option dicts) not at all."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, dict):
        meta = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        name = meta.get("name") or value.get("name")
        if name:
            space = meta.get("namespace")
            return f"{value.get('kind', '')} {space + '/' if space else ''}{name}".strip()
    return ""


def target_of(args):
    """The target column for a call's positional arguments: the described ones joined, empty ones dropped."""
    return " ".join(part for part in (describe(a) for a in args) if part)
