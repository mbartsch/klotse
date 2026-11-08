"""Schema help for the YAML editor (pure, no Qt, no network): field documentation, completion and unknown-field detection from the cluster's own
OpenAPI v3 documents (`/openapi/v3/api/v1`, `/openapi/v3/apis/GROUP/VERSION`), which describe the built-in kinds and the custom resources of that
cluster. Nothing is bundled: the descriptions are the ones the API server serves, so they match its version.

`Schema` navigates one document (`components.schemas`, `$ref` and `allOf` resolved) by a YAML path (keys, and `*` for a list element).
`yaml_path` finds where a line of text sits in the YAML by indentation, which works on text that does not parse yet (the point of completion).
Only what the document says is reported: a schema that allows arbitrary fields (`x-kubernetes-preserve-unknown-fields`, a free map) never
produces an unknown-field warning."""
import re

BUILTIN_PATHS = {  # kind -> OpenAPI v3 document path
    **dict.fromkeys(("Pod", "Service", "ConfigMap", "Secret", "Namespace", "Node", "PersistentVolume", "PersistentVolumeClaim", "ServiceAccount",
                     "Endpoints", "LimitRange", "ResourceQuota", "Event"), "/openapi/v3/api/v1"),
    **dict.fromkeys(("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"), "/openapi/v3/apis/apps/v1"),
    **dict.fromkeys(("Job", "CronJob"), "/openapi/v3/apis/batch/v1"),
    **dict.fromkeys(("Ingress", "NetworkPolicy", "IngressClass"), "/openapi/v3/apis/networking.k8s.io/v1"),
    **dict.fromkeys(("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding"), "/openapi/v3/apis/rbac.authorization.k8s.io/v1"),
    "HorizontalPodAutoscaler": "/openapi/v3/apis/autoscaling/v2", "PodDisruptionBudget": "/openapi/v3/apis/policy/v1",
    "StorageClass": "/openapi/v3/apis/storage.k8s.io/v1", "PriorityClass": "/openapi/v3/apis/scheduling.k8s.io/v1",
    "CustomResourceDefinition": "/openapi/v3/apis/apiextensions.k8s.io/v1",
    "ValidatingWebhookConfiguration": "/openapi/v3/apis/admissionregistration.k8s.io/v1",
    "MutatingWebhookConfiguration": "/openapi/v3/apis/admissionregistration.k8s.io/v1",
}


def openapi_path(kind, api_version=""):
    """The OpenAPI v3 document that describes `kind`: from its apiVersion when the manifest has one (`apps/v1`, `gateway.networking.k8s.io/v1`,
    `v1`), else from a table of the built-in kinds. None when it cannot be told."""
    if api_version:
        group, _, version = api_version.rpartition("/")
        return f"/openapi/v3/apis/{group}/{version}" if group else f"/openapi/v3/api/{version}"
    return BUILTIN_PATHS.get(kind)


class Schema:
    """One OpenAPI v3 document, navigated by YAML paths."""

    def __init__(self, document):
        self.schemas = ((document or {}).get("components") or {}).get("schemas") or {}

    # ---- resolving ------------------------------------------------------------------------------------------------
    def resolve(self, node, depth=0):
        """`node` with `$ref` and single-entry `allOf` followed (the API server wraps a described reference in `allOf`); the description of the
        outer node is kept when the target has none."""
        if not isinstance(node, dict) or depth > 12:
            return node or {}
        target = node.get("$ref") or next((part.get("$ref") for part in node.get("allOf") or [] if isinstance(part, dict) and part.get("$ref")), None)
        if not target:
            return node
        found = self.resolve(self.schemas.get(target.rsplit("/", 1)[-1], {}), depth + 1)
        merged = dict(found)
        if node.get("description") and not found.get("description"):
            merged["description"] = node["description"]
        elif node.get("description"):
            merged["description"] = node["description"]  # the field's own text is more specific than the type's
        return merged

    def root_for(self, kind, api_version=""):
        """The schema of the kind (found by `x-kubernetes-group-version-kind`), or {}."""
        group, _, version = api_version.rpartition("/") if api_version else ("", "", "")
        fallback = {}
        for schema in self.schemas.values():
            for gvk in schema.get("x-kubernetes-group-version-kind") or []:
                if gvk.get("kind") == kind:
                    if not api_version or (gvk.get("group", "") == group and gvk.get("version") == version):
                        return self.resolve(schema)
                    fallback = fallback or schema
        return self.resolve(fallback) if fallback else {}

    def child(self, node, token):
        """The schema of key `token` (or of a list element for `*`) under `node`, or None when the schema does not say."""
        node = self.resolve(node)
        if token == "*":
            return self.resolve(node["items"]) if isinstance(node.get("items"), dict) else None
        if token in (node.get("properties") or {}):
            return self.resolve(node["properties"][token])
        extra = node.get("additionalProperties")
        return self.resolve(extra) if isinstance(extra, dict) else None

    def walk(self, root, path):
        node = self.resolve(root)
        for token in path:
            node = self.child(node, token)
            if node is None:
                return None
        return node

    # ---- what to show ---------------------------------------------------------------------------------------------
    @staticmethod
    def type_of(node):
        if node.get("type") == "array":
            return "array"
        return node.get("type") or ("object" if node.get("properties") else "")

    def describe(self, root, path):
        """{name, type, format, description, enum, default, required, children} for the node at `path`, or None. `required` is whether the
        parent lists the key as required; `children` is [(name, type, required)] for an object."""
        node = self.walk(root, path)
        if node is None:
            return None
        parent = self.walk(root, path[:-1]) if path else None
        required = bool(path) and path[-1] in ((parent or {}).get("required") or [])
        children = [(name, self.type_of(self.resolve(sub)), name in (node.get("required") or []))
                    for name, sub in sorted((node.get("properties") or {}).items())]
        return {"name": path[-1] if path else "", "type": self.type_of(node), "format": node.get("format", ""), "description": node.get("description", ""),
                "enum": list(node.get("enum") or []), "default": node.get("default"), "required": required, "children": children}

    def fields(self, root, path):
        """[(name, type, required, description)] of the keys that can go under `path` (an object, or the elements of a list), required ones
        first. [] when the schema allows free keys or does not describe it."""
        node = self.walk(root, path)
        if node is None:
            return []
        if node.get("type") == "array":
            node = self.child(node, "*") or {}
        required = set(node.get("required") or [])
        out = [(name, self.type_of(self.resolve(sub)), name in required, self.resolve(sub).get("description", ""))
               for name, sub in (node.get("properties") or {}).items()]
        return sorted(out, key=lambda f: (not f[2], f[0]))

    def unknown_fields(self, data, root, limit=20):
        """Dotted paths of the keys in the parsed manifest `data` that the schema does not know, at most `limit`. Free-form parts are skipped."""
        found = []

        def visit(value, node, path):
            node = self.resolve(node) if node else {}
            if len(found) >= limit or not node or node.get("x-kubernetes-preserve-unknown-fields") or node.get("x-kubernetes-int-or-string"):
                return
            if isinstance(value, dict):
                props = node.get("properties")
                if props is None:
                    extra = node.get("additionalProperties")
                    for key, sub in value.items():
                        visit(sub, extra if isinstance(extra, dict) else None, path + [str(key)])
                    return
                for key, sub in value.items():
                    if key in props:
                        visit(sub, props[key], path + [str(key)])
                    elif not (node.get("additionalProperties") is True):
                        found.append(".".join(path + [str(key)]))
            elif isinstance(value, list) and isinstance(node.get("items"), dict):
                for index, item in enumerate(value):
                    visit(item, node["items"], path + [f"[{index}]"])

        visit(data, root, [])
        return found[:limit]


# ---- where a line sits in the YAML -----------------------------------------------------------------------------------
_KEY = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s#:'"-][^:#]*?|-[^\s:][^:#]*?)\s*:(?:\s|$)""")


def _split(line):
    """(indent of the first thing on the line, dash positions, key or None, text after the dashes) for one YAML line."""
    stripped = line.lstrip(" ")
    indent, dashes = len(line) - len(stripped), []
    while stripped.startswith("- ") or stripped == "-":
        dashes.append(indent)
        indent += 2
        stripped = stripped[2:].lstrip(" ")
        indent = len(line) - len(stripped)
    match = _KEY.match(stripped)
    key = match.group("key").strip("\"'") if match else None
    return indent, dashes, key, stripped


def yaml_path(lines, line_no):
    """(path through line `line_no` (0-based) including its own key, the indentation of that key, whether the line is a list item without a
    key): `["spec", "template", "spec", "containers", "*", "image"]`. Works from the indentation only, so text that is half typed is fine."""
    stack = []  # (indent, token)
    result = ([], 0, False)
    for number in range(min(line_no, len(lines) - 1) + 1):
        raw = lines[number]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent, dashes, key, _text = _split(raw)
        for dash in dashes:  # each dash is a list element at that indentation
            while stack and (stack[-1][0] > dash or (stack[-1][0] == dash and stack[-1][1] == "*")):
                stack.pop()
            stack.append((dash, "*"))
        if key is not None:
            while stack and stack[-1][0] >= indent and not (stack[-1][1] == "*" and stack[-1][0] < indent):
                stack.pop()
            stack.append((indent, key))
        result = ([token for _i, token in stack], indent, key is None and bool(dashes))
    return result


def parent_path(lines, line_no, indent):
    """The path of the object a key written at `indent` on line `line_no` would belong to (the line itself may be blank or half typed)."""
    probe = list(lines[:line_no]) + [" " * indent + "x:"]
    path, _indent, _item = yaml_path(probe, line_no)
    return path[:-1]


def sibling_keys(lines, line_no, indent):
    """The keys already written at `indent` in the block around `line_no` (same parent), so completion does not offer them again."""
    keys = set()
    for direction in (-1, 1):
        number = line_no + direction
        while 0 <= number < len(lines):
            raw = lines[number]
            if raw.strip() and not raw.lstrip().startswith("#"):
                other, dashes, key, _text = _split(raw)
                if other < indent:
                    break  # left the block: a shallower line
                if other == indent and dashes:  # a list element starts here
                    if direction == -1 and key is not None:
                        keys.add(key)  # the line that opens this element carries its first key (`- name: a`)
                    break  # going down it is the next element: not ours
                if other == indent and key is not None:
                    keys.add(key)
            number += direction
    return keys
