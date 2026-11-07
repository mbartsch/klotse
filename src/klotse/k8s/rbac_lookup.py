"""RBAC reverse lookup over the manifests of roles and bindings (pure, no Qt, no network).

`who_can` answers "which subjects may do this?"; `permissions_of` answers "what may this user, group or service account do?".
It follows what Kubernetes' RBAC authorizer does with Roles, ClusterRoles, RoleBindings and ClusterRoleBindings (a ClusterRole
bound by a RoleBinding only counts in that namespace). It does **not** know other authorizers (webhooks, cloud IAM mapped to
Kubernetes groups) or the API server's built-in superuser group `system:masters`, which is always allowed and has no binding. So
it is an answer from RBAC objects alone, which is why the dialog says "according to the RBAC objects"."""

SA_GROUPS = ("system:serviceaccounts", "system:authenticated")


def _meta(obj):
    return obj.get("metadata") or {}


def rule_matches(rule, verb, group, resource, name=""):
    """None when the rule does not cover the request, else "all" (covers it for every object) or "named" (only for some named
    objects, which cover the request only when `name` is one of them, or when no name was asked for: then it is "named")."""
    verbs, groups, resources = rule.get("verbs") or [], rule.get("apiGroups") or [], rule.get("resources") or []
    if "*" not in verbs and verb not in verbs:
        return None
    if "*" not in groups and group not in groups:
        return None
    if not any(_resource_covers(r, resource) for r in resources):
        return None
    names = rule.get("resourceNames") or []
    if not names:
        return "all"
    return "all" if name and name in names else ("named" if not name else None)


def _resource_covers(rule_resource, resource):
    if rule_resource == "*" or rule_resource == resource:
        return True
    if "/" in resource:
        if rule_resource.endswith("/*") and resource.startswith(rule_resource[:-1]):
            return True  # "pods/*" covers "pods/log"
        if rule_resource.startswith("*/") and resource.endswith(rule_resource[1:]):
            return True  # "*/scale" covers "deployments/scale"
    return False


def url_matches(rule, verb, url):
    verbs = rule.get("verbs") or []
    if "*" not in verbs and verb not in verbs:
        return False
    for pattern in rule.get("nonResourceURLs") or []:
        if pattern == "*" or pattern == url or (pattern.endswith("*") and url.startswith(pattern[:-1])):
            return True
    return False


class Rbac:
    """The roles and bindings of a cluster, indexed for the two questions. Build with the manifest lists, then ask."""

    def __init__(self, roles=(), clusterroles=(), rolebindings=(), clusterrolebindings=()):
        self.roles = {(_meta(r).get("namespace", ""), _meta(r).get("name", "")): r for r in roles}
        self.clusterroles = {_meta(r).get("name", ""): r for r in clusterroles}
        self.rolebindings = list(rolebindings)
        self.clusterrolebindings = list(clusterrolebindings)

    def rules_of(self, ref, namespace):
        """(rules, found) for a binding's roleRef; a Role is looked up in the binding's namespace."""
        kind, name = ref.get("kind"), ref.get("name", "")
        role = self.clusterroles.get(name) if kind == "ClusterRole" else self.roles.get((namespace, name)) if kind == "Role" else None
        return (role.get("rules") or [] if role else []), role is not None

    def bindings(self):
        """[(binding, scope namespace or "" for cluster-wide)]."""
        return [(b, "") for b in self.clusterrolebindings] + [(b, _meta(b).get("namespace", "")) for b in self.rolebindings]

    # ---- who can do X? -------------------------------------------------------------------------------------------
    def who_can(self, verb, group, resource, namespace="", name=""):
        """[{kind, name, namespace, via, scope, scope_namespace, limited, wide, missing}] for the subjects granted `verb` on
        `resource` (`pods`, `pods/log`) of API group `group` ("" = core). With a `namespace`, cluster-wide bindings and the
        bindings of that namespace count; without one, only cluster-wide bindings (a RoleBinding grants inside one namespace).
        `limited` = only for named objects; `wide` = granted to a whole group such as system:authenticated."""
        found = {}
        for binding, scope_ns in self.bindings():
            if scope_ns and scope_ns != namespace:
                continue
            rules, exists = self.rules_of(binding.get("roleRef") or {}, scope_ns)
            how = [rule_matches(r, verb, group, resource, name) for r in rules]
            how = [h for h in how if h]
            if not how:
                continue
            ref = binding.get("roleRef") or {}
            via = f"{'RoleBinding' if scope_ns else 'ClusterRoleBinding'} {_meta(binding).get('name', '')} → {ref.get('kind')} {ref.get('name')}"
            for subject in binding.get("subjects") or []:
                key = (subject.get("kind"), subject.get("name"), subject.get("namespace", ""))
                entry = found.setdefault(key, {"kind": key[0], "name": key[1], "namespace": key[2], "via": [], "scope": "cluster-wide" if not scope_ns else f"namespace {scope_ns}",
                                               "scope_namespace": scope_ns, "limited": True,
                                               "wide": key[0] == "Group" and key[1] in SA_GROUPS + ("system:unauthenticated",)})
                entry["via"].append(via)
                if not scope_ns:
                    entry["scope"], entry["scope_namespace"] = "cluster-wide", ""
                if "all" in how:
                    entry["limited"] = False
        out = list(found.values())
        for entry in out:
            entry["via"] = "; ".join(sorted(set(entry["via"])))
        return sorted(out, key=lambda e: (not e["wide"], e["kind"] or "", e["name"] or "", e["namespace"]))

    # ---- what can they do? ---------------------------------------------------------------------------------------
    def subject_matches(self, subject, kind, name, namespace=""):
        """Does a binding's `subject` stand for the user, group or service account asked about (including the groups every
        service account belongs to)?"""
        s_kind, s_name = subject.get("kind"), subject.get("name")
        if kind == "ServiceAccount":
            if s_kind == "ServiceAccount":
                return s_name == name and subject.get("namespace", "") == namespace
            return s_kind == "Group" and s_name in SA_GROUPS + (f"system:serviceaccounts:{namespace}",)
        if kind == "User":
            return (s_kind == "User" and s_name == name) or (s_kind == "Group" and s_name == "system:authenticated")
        if kind == "Group":
            return s_kind == "Group" and s_name == name
        return False

    def permissions_of(self, kind, name, namespace=""):
        """[{scope, via, group_via, rules, role_missing}]: one entry per binding that reaches the subject, with the role's rules.
        `group_via` is the group that gave it (empty when the subject is named directly)."""
        out = []
        for binding, scope_ns in self.bindings():
            matched = [s for s in binding.get("subjects") or [] if self.subject_matches(s, kind, name, namespace)]
            if not matched:
                continue
            ref = binding.get("roleRef") or {}
            rules, exists = self.rules_of(ref, scope_ns)
            direct = any(s.get("kind") == kind for s in matched)
            out.append({"scope": f"namespace {scope_ns}" if scope_ns else "cluster-wide", "scope_namespace": scope_ns,
                        "via": f"{'RoleBinding' if scope_ns else 'ClusterRoleBinding'} {_meta(binding).get('name', '')} → {ref.get('kind')} {ref.get('name')}",
                        "group_via": "" if direct else ", ".join(sorted({s.get("name", "") for s in matched})),
                        "rules": rules, "role_missing": not exists})
        return sorted(out, key=lambda e: (e["scope_namespace"] != "", e["scope"], e["via"]))


def rule_rows(entries):
    """Flat table rows (scope, via, given through, verbs, resources or URLs, API groups, names) from `permissions_of`."""
    rows = []
    for entry in entries:
        if entry["role_missing"]:
            rows.append((entry["scope"], entry["via"], entry["group_via"], "", "(the role does not exist)", "", ""))
        for rule in entry["rules"]:
            what = ", ".join(rule.get("resources") or []) or ", ".join(rule.get("nonResourceURLs") or [])
            groups = ", ".join(g or "(core)" for g in rule.get("apiGroups") or [])
            rows.append((entry["scope"], entry["via"], entry["group_via"], ", ".join(rule.get("verbs") or []), what, groups,
                         ", ".join(rule.get("resourceNames") or [])))
    return rows
