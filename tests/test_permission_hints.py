"""Hints on menu entries when the user's rules say an action is not allowed (issue #54). Only False is ever shown; the entry
stays clickable and nothing is hidden, because the rules the server reports can be incomplete."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from klotse.k8s.models import NodeRow, PodRow
from klotse.k8s.permissions import ACTIONS, Permissions, resource_for_kind
from klotse.ui.workspace import ClusterWorkspace


def rule(verbs, groups=("",), resources=("pods",), names=()):
    return {"verbs": list(verbs), "groups": list(groups), "resources": list(resources), "names": list(names)}


def perms(*rules, incomplete=False, error="", ns="shop"):
    return Permissions({"resources": list(rules), "urls": [], "incomplete": incomplete, "error": error}, ns)


# ---- the rules ---------------------------------------------------------------------------------------------------
def test_a_matching_rule_allows_and_complete_rules_without_one_deny():
    p = perms(rule(["get", "list"], resources=["pods"]))
    assert p.check("get", "", "pods") is True
    assert p.check("delete", "", "pods") is False  # complete rules, nothing allows it
    assert p.check("get", "apps", "deployments") is False


def test_wildcards_match_verbs_groups_and_resources():
    assert perms(rule(["*"], ["*"], ["*"])).check("delete", "apps", "deployments") is True
    assert perms(rule(["get"], ["apps"], ["*"])).check("get", "apps", "statefulsets") is True
    assert perms(rule(["get"], ["apps"], ["*"])).check("get", "", "pods") is False


def test_subresources_need_their_own_rule():
    assert perms(rule(["create"], resources=["pods/exec"])).check("create", "", "pods", "exec") is True
    assert perms(rule(["create"], resources=["pods"])).check("create", "", "pods", "exec") is False  # pods is not pods/exec
    assert perms(rule(["update"], ["apps"], ["deployments/scale"])).check("update", "apps", "deployments", "scale") is True
    assert perms(rule(["get"], resources=["*/log"])).check("get", "", "pods", "log") is True
    assert perms(rule(["get"], resources=["pods/*"])).check("get", "", "pods", "log") is True


def test_incomplete_or_failed_rules_never_deny():
    for p in (perms(rule(["get"]), incomplete=True), perms(rule(["get"]), error="webhook down")):
        assert p.check("delete", "", "pods") is None and p.check("get", "", "pods") is True


def test_a_rule_limited_to_named_objects_does_not_answer_for_the_kind():
    p = perms(rule(["delete"], resources=["pods"], names=["only-this"]))
    assert p.check("delete", "", "pods") is None  # may be allowed for that pod and not for the others
    assert perms(rule(["delete"], resources=["pods"], names=["x"]), rule(["delete"], resources=["pods"])).check("delete", "", "pods") is True


def test_kinds_map_to_their_group_and_plural():
    assert resource_for_kind("Pod") == ("", "pods") and resource_for_kind("Deployment") == ("apps", "deployments")
    assert resource_for_kind("Role") == ("rbac.authorization.k8s.io", "roles") and resource_for_kind("Ingress") == ("networking.k8s.io", "ingresses")
    assert resource_for_kind("HorizontalPodAutoscaler") == ("autoscaling", "horizontalpodautoscalers")
    assert resource_for_kind("Container") is None and resource_for_kind("Widget") is None


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_every_action_is_answered_for_a_kind_it_applies_to(action):
    kind = {"cordon": "Node", "delete-node": "Node", "terminate": "Pod", "shell": "Pod", "attach": "Pod", "logs": "Pod"}.get(action, "Deployment")
    assert perms().allowed_action(kind, action) is False
    assert perms(rule(["*"], ["*"], ["*"])).allowed_action(kind, action) is True


def test_unknown_kinds_and_actions_are_not_judged():
    p = perms()
    assert p.allowed_action("Widget", "delete") is None and p.allowed_action("Pod", "teleport") is None


def test_the_deployment_scale_and_restart_rules_are_what_kubectl_needs():
    p = perms(rule(["get", "list", "patch"], ["apps"], ["deployments"]), rule(["update"], ["apps"], ["deployments/scale"]))
    assert p.allowed_action("Deployment", "restart") is True and p.allowed_action("Deployment", "scale") is True
    assert p.allowed_action("Deployment", "delete") is False and p.allowed_action("Deployment", "edit") is False


# ---- the workspace -----------------------------------------------------------------------------------------------
READER = {"resources": [rule(["get", "list", "watch"], ["*"], ["*"])], "urls": [], "incomplete": False, "error": ""}


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.resize(1000, 600)
    return ws


def pod(ns="shop"):
    return PodRow("web-1", ns, "1/1", "Running", 0, "n1", "1d", {"metadata": {"name": "web-1", "namespace": ns}})


def test_the_rules_are_fetched_for_the_namespace_in_front_and_follow_it(qtbot):
    asked = []
    ws = make_ws(qtbot, my_permissions=lambda ns: asked.append(ns) or READER)
    ws._ready = True
    ws._set_namespaces([SimpleNamespace(name="default", status="Active", age="1d", labels="", raw={}),
                        SimpleNamespace(name="shop", status="Active", age="1d", labels="", raw={})])
    ws._load_permissions()
    qtbot.waitUntil(lambda: ws.permissions is not None, timeout=5000)
    assert ws.permissions.namespace == "default" and asked[-1] == "default"
    ws.set_namespace("shop")
    qtbot.waitUntil(lambda: ws.permissions is not None and ws.permissions.namespace == "shop", timeout=5000)


def test_all_namespaces_gives_no_hints_and_a_failure_gives_none_either(qtbot):
    ws = make_ws(qtbot, my_permissions=lambda ns: READER)
    ws.permissions = Permissions(READER, "default")
    ws.ns_combo.setCurrentIndex(0)  # All namespaces
    ws._load_permissions()
    assert ws.permissions is None and ws.permission_for("Pod", "delete", pod("default")) is None

    def boom(ns):
        raise RuntimeError("forbidden")
    failing = make_ws(qtbot, my_permissions=boom)
    failing.permissions = Permissions(READER, "default")
    failing._load_permissions()
    qtbot.waitUntil(lambda: failing.permissions is None, timeout=5000)


def test_a_service_that_cannot_say_just_means_no_hints(qtbot):
    ws = make_ws(qtbot)
    ws._load_permissions()
    assert ws.permissions is None and ws.permission_for("Pod", "delete", pod()) is None


def test_a_row_from_another_namespace_is_not_judged_but_a_cluster_wide_kind_is(qtbot):
    ws = make_ws(qtbot)
    ws.permissions = Permissions(READER, "shop")
    assert ws.permission_for("Pod", "delete", pod("shop")) is False
    assert ws.permission_for("Pod", "delete", pod("elsewhere")) is None  # these rules are for shop only
    assert ws.permission_for("Node", "cordon", SimpleNamespace(name="n1")) is False  # cluster-scoped: no namespace on the row


def test_an_answer_for_a_namespace_the_user_has_left_is_dropped(qtbot):
    ws = make_ws(qtbot)
    ws.ns_combo.setCurrentIndex(1)  # default
    ws._set_permissions("elsewhere", READER)
    assert ws.permissions is None
    ws._set_permissions("default", READER)
    assert ws.permissions.namespace == "default"


# ---- the menu ----------------------------------------------------------------------------------------------------
def menu_texts(table, row_index=0):
    """The entries of the table's right-click menu for a row: the popup is read and closed from inside its own event loop."""
    texts = []

    def grab():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            texts.extend(a.text() for a in popup.actions() if not a.isSeparator())
            popup.close()
    QTimer.singleShot(100, grab)
    table._context_menu(table.view.visualRect(table.proxy.index(row_index, 0)).center())
    return texts


def test_menu_entries_the_rules_forbid_say_so_and_the_rest_do_not(qtbot):
    ws = make_ws(qtbot)
    ws.show()
    ws.permissions = Permissions({"resources": [rule(["get", "list"], [""], ["pods"]), rule(["create"], [""], ["pods/exec"])],
                                  "urls": [], "incomplete": False, "error": ""}, "shop")
    ws.pod_table.set_rows([pod()])
    texts = menu_texts(ws.pod_table)
    assert "Logs  (not allowed for you)" in texts  # get on pods is granted, get on pods/log is a separate permission
    assert "Connect (shell)…" in texts  # pods/exec create is granted
    assert "Terminate…  (not allowed for you)" in texts and "Force terminate…  (not allowed for you)" in texts
    assert "Attach…  (not allowed for you)" in texts


def test_without_rules_or_with_incomplete_ones_the_menu_is_unchanged(qtbot):
    ws = make_ws(qtbot)
    ws.show()
    ws.pod_table.set_rows([pod()])
    plain = menu_texts(ws.pod_table)
    assert plain and not any("not allowed" in t for t in plain)
    ws.permissions = Permissions({"resources": [], "urls": [], "incomplete": True, "error": ""}, "shop")
    assert menu_texts(ws.pod_table) == plain


def test_node_entries_follow_the_cluster_wide_rules(qtbot):
    ws = make_ws(qtbot)
    ws.show()
    ws.permissions = Permissions(READER, "shop")
    ws.node_table.set_rows([NodeRow("n1", "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"metadata": {"name": "n1"}})])
    texts = menu_texts(ws.node_table)
    assert "Cordon…  (not allowed for you)" in texts and "Delete node…  (not allowed for you)" in texts
    assert "List pods on this node" in texts
