"""Compare two objects as YAML (pure, no Qt). Fields that always differ between two objects, or that the cluster fills in, can be
left out so the real differences stand out."""
import copy
import difflib

NOISE_METADATA = ("managedFields", "resourceVersion", "uid", "creationTimestamp", "generation", "selfLink", "deletionTimestamp")
LAST_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"


def cleaned(raw, noise=True, status=False, identity=False):
    """A copy of a manifest dict without `noise` (managedFields, resourceVersion, uid, timestamps, generation, the last-applied
    annotation), without `status` unless asked, and without name and namespace when `identity` is True."""
    body = copy.deepcopy(raw or {})
    meta = body.get("metadata")
    if isinstance(meta, dict):
        if noise:
            for key in NOISE_METADATA:
                meta.pop(key, None)
            annotations = meta.get("annotations")
            if isinstance(annotations, dict):
                annotations.pop(LAST_APPLIED, None)
                if not annotations:
                    meta.pop("annotations")
        if identity:
            meta.pop("name", None)
            meta.pop("namespace", None)
    if not status:
        body.pop("status", None)
    return body


def diff_lines(left_text, right_text, left_name, right_name):
    """Unified diff lines (no trailing newlines); empty when the texts are the same."""
    return list(difflib.unified_diff(left_text.splitlines(), right_text.splitlines(), left_name, right_name, lineterm="", n=3))


def stats(lines):
    """(added, removed) line counts of `diff_lines` output, headers excluded."""
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return added, removed
