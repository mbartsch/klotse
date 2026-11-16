"""#656 (A2): a rule that names objects does not allow a request that has no object name in its URL (create, deletecollection,
list, watch). A named get or update still matches the name it lists."""
from klotse.k8s.rbac_lookup import rule_matches


def named(verbs, names=("demo",)):
    return {"verbs": verbs, "apiGroups": [""], "resources": ["pods"], "resourceNames": list(names)}


def test_a_named_rule_does_not_allow_a_top_level_create():
    assert rule_matches(named(["create"]), "create", "", "pods", "demo") is None
    assert rule_matches(named(["create"]), "create", "", "pods", "") is None


def test_a_named_rule_does_not_allow_deletecollection_list_or_watch():
    for verb in ("deletecollection", "list", "watch"):
        assert rule_matches(named([verb]), verb, "", "pods", "demo") is None


def test_a_named_get_still_covers_the_named_object_only():
    assert rule_matches(named(["get"]), "get", "", "pods", "demo") == "all"
    assert rule_matches(named(["get"]), "get", "", "pods", "other") is None
    assert rule_matches(named(["get"]), "get", "", "pods", "") == "named"


def test_an_unnamed_create_rule_still_covers_the_create():
    rule = {"verbs": ["create"], "apiGroups": [""], "resources": ["pods"]}
    assert rule_matches(rule, "create", "", "pods", "demo") == "all"
