"""#655 (A1): "pods/*" is not a wildcard in Kubernetes; a subresource is granted by "*", its exact name or "*/<subresource>"."""
from klotse.k8s.permissions import _resource_matches
from klotse.k8s.rbac_lookup import _resource_covers


def test_pods_star_does_not_grant_a_subresource_in_the_lookup():
    assert _resource_covers("pods/*", "pods/log") is False


def test_the_real_wildcards_still_grant_a_subresource():
    assert _resource_covers("*", "pods/log") is True
    assert _resource_covers("*/log", "pods/log") is True
    assert _resource_covers("pods/log", "pods/log") is True


def test_pods_star_does_not_grant_a_subresource_in_the_permission_check():
    assert _resource_matches(["pods/*"], "pods", "log") is False
    assert _resource_matches(["pods/log"], "pods", "log") is True
    assert _resource_matches(["*/log"], "pods", "log") is True
