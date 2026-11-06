"""What the signed-in user may do, worked out from the rules the API server reports for a namespace (SelfSubjectRulesReview).
Pure functions and one small class; no cluster access, no Qt.

The rules can be incomplete (the server says so, or some authorizers cannot list their rules), so every answer is
True (a rule allows it), False (the rules are complete and nothing allows it) or None (cannot tell). Only False is ever shown
to the user, as a hint on a menu entry; nothing is hidden or switched off because of it."""
from .resources import SPEC_BY_KIND

API_GROUPS = {"core": "", "apps": "apps", "batch": "batch", "net": "networking.k8s.io", "storage": "storage.k8s.io",
              "rbac": "rbac.authorization.k8s.io", "ext": "apiextensions.k8s.io", "autoscaling": "autoscaling", "policy": "policy",
              "scheduling": "scheduling.k8s.io", "admission": "admissionregistration.k8s.io", "discovery": "discovery.k8s.io",
              "coordination": "coordination.k8s.io"}
BUILT_IN = {"Pod": ("", "pods"), "Node": ("", "nodes"), "Namespace": ("", "namespaces"), "Deployment": ("apps", "deployments"),
            "ReplicaSet": ("apps", "replicasets"), "StatefulSet": ("apps", "statefulsets")}

# what each menu action needs: (verb, subresource). "restart" and "cordon" are patches; scaling goes through the scale subresource.
ACTIONS = {"edit": ("update", ""), "create": ("create", ""), "delete": ("delete", ""), "scale": ("update", "scale"),
           "restart": ("patch", ""), "terminate": ("delete", ""), "shell": ("create", "exec"), "attach": ("create", "attach"),
           "logs": ("get", "log"), "cordon": ("patch", ""), "delete-node": ("delete", "")}


def resource_for_kind(kind):
    """(API group, plural resource name) of a kind the lists show, or None for one we cannot map."""
    if kind in BUILT_IN:
        return BUILT_IN[kind]
    spec = SPEC_BY_KIND.get(kind)
    if spec is not None and spec.api in API_GROUPS:
        return API_GROUPS[spec.api], spec.key  # the registry key is the plural ("daemonsets", "networkpolicies", ...)
    return None


def _matches(listed, wanted):
    return "*" in listed or wanted in listed


def _resource_matches(listed, resource, subresource):
    if "*" in listed:
        return True
    if not subresource:
        return resource in listed
    return f"{resource}/{subresource}" in listed or f"*/{subresource}" in listed or f"{resource}/*" in listed


class Permissions:
    """The user's rules in one namespace (the dict `KubeService.my_permissions` returns)."""

    def __init__(self, data, namespace=""):
        data = data or {}
        self.namespace = namespace
        self.rules = data.get("resources") or []
        self.complete = not data.get("incomplete") and not data.get("error")

    def check(self, verb, group, resource, subresource=""):
        """True when a rule allows it, False when the rules are complete and none does, else None. A rule limited to named
        objects does not answer for the kind as a whole (it may allow this object and not that one): that is None."""
        named = False
        for rule in self.rules:
            if not (_matches(rule.get("verbs") or [], verb) and _matches(rule.get("groups") or [], group)
                    and _resource_matches(rule.get("resources") or [], resource, subresource)):
                continue
            if rule.get("names"):
                named = True
                continue
            return True
        if named:
            return None
        return False if self.complete else None

    def allowed_action(self, kind, action):
        """check() for a menu action (`ACTIONS`) on a kind; None for a kind or action we have no mapping for."""
        needs, found = ACTIONS.get(action), resource_for_kind(kind)
        if needs is None or found is None:
            return None
        return self.check(needs[0], found[0], found[1], needs[1])
