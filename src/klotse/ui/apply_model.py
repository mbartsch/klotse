"""The data side of File > Apply YAML…, no Qt: reading the documents the user typed or opened. Each document must be a
Kubernetes object (apiVersion, kind, metadata.name); problems are listed per document so the dialog can say exactly what to
fix before anything is sent to the cluster."""
import yaml

MAX_TEXT = 2_000_000  # characters; a bigger paste is almost certainly not a hand-written manifest
MAX_DOCUMENTS = 100


class ApplyProblem(ValueError):
    """The text cannot be applied; the message says why and where."""


def parse_documents(text):
    """[dict, ...] from a (multi-document) YAML text. Empty documents (a lone `---`) are skipped. Raises ApplyProblem for
    text that is too big or not YAML, and for any document that is not a Kubernetes object."""
    if len(text) > MAX_TEXT:
        raise ApplyProblem(f"The text is too long ({len(text):,} characters; the limit is {MAX_TEXT:,}).")
    try:
        loaded = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise ApplyProblem(f"This is not valid YAML{where}: {getattr(e, 'problem', None) or e}") from e
    if not loaded:
        raise ApplyProblem("There is nothing to apply: the text has no YAML document.")
    if len(loaded) > MAX_DOCUMENTS:
        raise ApplyProblem(f"Too many documents ({len(loaded)}); the limit is {MAX_DOCUMENTS}.")
    docs, problems = [], []
    for number, doc in enumerate(loaded, 1):
        found = _check(doc)
        if found:
            problems.append(f"Document {number}: {found}")
        else:
            docs.append(doc)
    if problems:
        raise ApplyProblem("\n".join(problems))
    items = _expand_lists(docs)
    bad = [f"Item {n} of a List: {found}" for n, d in enumerate(items, 1) if (found := _check(d))]
    if bad:
        raise ApplyProblem("\n".join(bad))
    return items


def _check(doc):
    if not isinstance(doc, dict):
        return "is not a mapping (a Kubernetes object is `key: value` pairs)."
    if doc.get("kind") == "List" and isinstance(doc.get("items"), list):
        return None  # what `kubectl get -o yaml` prints: its items are checked when they are expanded
    missing = [k for k in ("apiVersion", "kind") if not isinstance(doc.get(k), str) or not doc.get(k)]
    meta = doc.get("metadata")
    name = meta.get("name") if isinstance(meta, dict) else None
    if not name and isinstance(meta, dict) and meta.get("generateName"):
        return "uses generateName, which needs a create and cannot be applied; give it a name."
    if not isinstance(name, str) or not name:
        missing.append("metadata.name")
    return f"is missing {', '.join(missing)}." if missing else None


def _expand_lists(docs):
    """A `kind: List` document (what `kubectl get -o yaml` prints) stands for its items."""
    out = []
    for doc in docs:
        if doc.get("kind") == "List" and isinstance(doc.get("items"), list):
            out.extend(i for i in doc["items"] if isinstance(i, dict))
        else:
            out.append(doc)
    return out


def label(doc, default_namespace=""):
    """'Deployment shop/web' (or 'Namespace dev' for an object without a namespace)."""
    meta = doc.get("metadata") or {}
    ns = meta.get("namespace") or default_namespace
    return f"{doc.get('kind', '?')} {ns + '/' if ns else ''}{meta.get('name', '?')}"
