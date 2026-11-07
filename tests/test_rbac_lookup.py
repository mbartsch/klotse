from types import SimpleNamespace

from klotse.k8s.client import KubeService
from klotse.k8s.rbac_lookup import Rbac, rule_matches, rule_rows, url_matches
from klotse.ui.rbac_dialog import RbacDialog
from klotse.ui.workspace import ClusterWorkspace


def role(name, rules, ns=None):
    meta = {"name": name}
    if ns:
        meta["namespace"] = ns
    return {"metadata": meta, "rules": rules}


def binding(name, ref_kind, ref_name, subjects, ns=None):
    meta = {"name": name}
    if ns:
        meta["namespace"] = ns
    return {"metadata": meta, "roleRef": {"kind": ref_kind, "name": ref_name}, "subjects": subjects}


def rule(verbs, resources, groups=("",), names=None, urls=None):
    r = {"verbs": list(verbs), "apiGroups": list(groups), "resources": list(resources)}
    if names:
        r["resourceNames"] = names
    if urls:
        r["nonResourceURLs"] = urls
    return r


def cluster():
    return Rbac(
        roles=[role("pod-reader", [rule(["get", "list"], ["pods"])], "shop"), role("pod-reader", [rule(["get", "list"], ["pods"])], "other"),
               role("secret-peek", [rule(["get"], ["secrets"], names=["db-password"])], "shop")],
        clusterroles=[role("view-all", [rule(["get", "list", "watch"], ["*"], groups=["*"])]),
                      role("log-reader", [rule(["get"], ["pods/log"])]),
                      role("deployer", [rule(["*"], ["deployments", "deployments/scale"], groups=["apps"])]),
                      role("metrics", [rule(["get"], [], groups=[], urls=["/metrics", "/healthz/*"])])],
        rolebindings=[binding("rb-reader", "Role", "pod-reader", [{"kind": "User", "name": "alice"}], "shop"),
                      binding("rb-secret", "Role", "secret-peek", [{"kind": "ServiceAccount", "name": "app", "namespace": "shop"}], "shop"),
                      binding("rb-logs", "ClusterRole", "log-reader", [{"kind": "Group", "name": "devs"}], "shop"),
                      binding("rb-other", "Role", "pod-reader", [{"kind": "User", "name": "carol"}], "other")],
        clusterrolebindings=[binding("crb-view", "ClusterRole", "view-all", [{"kind": "Group", "name": "auditors"}]),
                             binding("crb-deploy", "ClusterRole", "deployer", [{"kind": "ServiceAccount", "name": "ci", "namespace": "tools"}]),
                             binding("crb-everyone", "ClusterRole", "log-reader", [{"kind": "Group", "name": "system:authenticated"}])])


def names(entries):
    return {(e["kind"], e["name"]) for e in entries}


def test_rule_matching_verbs_groups_resources_subresources_and_names():
    assert rule_matches(rule(["get"], ["pods"]), "get", "", "pods") == "all"
    assert rule_matches(rule(["get"], ["pods"]), "list", "", "pods") is None
    assert rule_matches(rule(["*"], ["*"], groups=["*"]), "delete", "apps", "deployments") == "all"
    assert rule_matches(rule(["get"], ["pods"]), "get", "", "pods/log") is None          # a subresource needs its own rule
    assert rule_matches(rule(["get"], ["pods/log"]), "get", "", "pods/log") == "all"
    assert rule_matches(rule(["get"], ["pods/*"]), "get", "", "pods/exec") == "all"
    assert rule_matches(rule(["update"], ["*/scale"], groups=["apps"]), "update", "apps", "deployments/scale") == "all"
    assert rule_matches(rule(["get"], ["pods"], groups=["apps"]), "get", "", "pods") is None
    named = rule(["get"], ["secrets"], names=["db-password"])
    assert rule_matches(named, "get", "", "secrets") == "named"                          # no name asked: only some objects
    assert rule_matches(named, "get", "", "secrets", "db-password") == "all"
    assert rule_matches(named, "get", "", "secrets", "other") is None


def test_non_resource_urls():
    r = rule(["get"], [], urls=["/metrics", "/healthz/*"])
    assert url_matches(r, "get", "/metrics") and url_matches(r, "get", "/healthz/ready")
    assert not url_matches(r, "post", "/metrics") and not url_matches(r, "get", "/other")


def test_who_can_cluster_wide_and_in_a_namespace():
    c = cluster()
    anywhere = c.who_can("get", "", "pods")
    assert names(anywhere) == {("Group", "auditors")}                                    # only cluster-wide grants without a namespace
    in_shop = c.who_can("get", "", "pods", "shop")
    assert ("User", "alice") in names(in_shop) and ("Group", "auditors") in names(in_shop)
    assert ("User", "carol") not in names(in_shop)                                       # her RoleBinding is in another namespace
    assert ("User", "carol") in names(c.who_can("get", "", "pods", "other"))
    alice = next(e for e in in_shop if e["name"] == "alice")
    assert alice["via"] == "RoleBinding rb-reader → Role pod-reader" and alice["scope"] == "namespace shop" and not alice["limited"]
    auditors = next(e for e in in_shop if e["name"] == "auditors")
    assert auditors["scope"] == "cluster-wide" and "ClusterRoleBinding crb-view" in auditors["via"]


def test_who_can_marks_wide_groups_and_named_only_grants():
    c = cluster()
    logs = c.who_can("get", "", "pods/log", "shop")
    assert logs[0]["name"] == "system:authenticated" and logs[0]["wide"]                # wide grants are listed first
    assert {e["name"] for e in logs} == {"system:authenticated", "devs", "auditors"}  # auditors' view-all has "*" resources
    secrets = c.who_can("get", "", "secrets", "shop")
    app = next(e for e in secrets if e["name"] == "app")
    assert app["limited"] is True                                                        # only db-password
    assert next(e for e in c.who_can("get", "", "secrets", "shop", "db-password") if e["name"] == "app")["limited"] is False
    assert ("ServiceAccount", "app") not in names(c.who_can("get", "", "secrets", "shop", "other"))


def test_who_can_with_wildcard_verbs_and_subresources():
    c = cluster()
    assert ("ServiceAccount", "ci") in names(c.who_can("delete", "apps", "deployments"))
    assert ("ServiceAccount", "ci") in names(c.who_can("update", "apps", "deployments/scale"))
    assert ("ServiceAccount", "ci") not in names(c.who_can("delete", "apps", "statefulsets"))


def test_permissions_of_a_service_account_include_its_groups():
    c = cluster()
    entries = c.permissions_of("ServiceAccount", "app", "shop")
    vias = {e["via"]: e for e in entries}
    assert "RoleBinding rb-secret → Role secret-peek" in vias and vias["RoleBinding rb-secret → Role secret-peek"]["group_via"] == ""
    wide = vias["ClusterRoleBinding crb-everyone → ClusterRole log-reader"]
    assert wide["group_via"] == "system:authenticated" and wide["scope"] == "cluster-wide"
    assert c.permissions_of("ServiceAccount", "app", "elsewhere") != c.permissions_of("ServiceAccount", "app", "shop")  # namespace matters
    assert c.permissions_of("ServiceAccount", "nobody", "x")[0]["group_via"] == "system:authenticated"  # still in system:authenticated


def test_permissions_of_users_and_groups_and_rule_rows():
    c = cluster()
    alice = c.permissions_of("User", "alice")
    assert {e["via"] for e in alice} == {"RoleBinding rb-reader → Role pod-reader", "ClusterRoleBinding crb-everyone → ClusterRole log-reader"}
    assert [e["via"] for e in c.permissions_of("Group", "auditors")] == ["ClusterRoleBinding crb-view → ClusterRole view-all"]
    rows = rule_rows(c.permissions_of("Group", "auditors"))
    assert rows == [("cluster-wide", "ClusterRoleBinding crb-view → ClusterRole view-all", "", "get, list, watch", "*", "*", "")]
    missing = Rbac(rolebindings=[binding("b", "Role", "gone", [{"kind": "User", "name": "x"}], "ns")])
    row = rule_rows(missing.permissions_of("User", "x"))[0]
    assert "does not exist" in row[4]


def test_service_reads_the_four_kinds_and_names_a_failure():
    service = KubeService.__new__(KubeService)

    def list_resource(key, namespace=None):
        if key == "clusterroles":
            raise PermissionError("forbidden")
        return [SimpleNamespace(raw={"kind": key})]
    service.list_resource = list_resource
    data = service.rbac_data()
    assert data["roles"] == [{"kind": "roles"}] and data["clusterroles"] == [] and data["missing"] == ["clusterroles (PermissionError)"]


def data_of(rbac):
    return {"roles": list(rbac.roles.values()), "clusterroles": list(rbac.clusterroles.values()), "rolebindings": rbac.rolebindings,
            "clusterrolebindings": rbac.clusterrolebindings}


def test_dialog_answers_both_questions(qtbot):
    dialog = RbacDialog("ctx")
    qtbot.addWidget(dialog)
    assert not dialog.ask.isEnabled()
    dialog.show_data({**data_of(cluster()), "missing": ["roles (Forbidden)"]})
    assert "3 Roles, 4 ClusterRoles, 4 RoleBindings, 3 ClusterRoleBindings" in dialog.status.text() and "incomplete" in dialog.status.text()
    dialog.verb.setCurrentText("get")
    dialog.run_who_can()
    assert "Type a resource" in dialog.who_result.text()
    dialog.resource.setText("pods")
    dialog.namespace.setText("shop")
    dialog.run_who_can()
    assert "can get pods in namespace shop" in dialog.who_result.text() and dialog.who_table.rowCount() >= 2
    dialog.resource.setText("nothings")
    dialog.verb.setCurrentText("delete")
    dialog.run_who_can()
    assert "No subject can delete nothings" in dialog.who_result.text() and dialog.who_table.rowCount() == 0
    dialog.subject_name.setText("auditors")
    dialog.subject_kind.setCurrentText("Group")
    dialog.run_permissions()
    assert dialog.perm_table.rowCount() == 1 and "1 binding reaches Group auditors" in dialog.perm_result.text()
    dialog.subject_kind.setCurrentText("ServiceAccount")
    dialog.subject_name.setText("app")
    dialog.run_permissions()
    assert "needs its namespace" in dialog.perm_result.text()
    dialog.show_error("boom")
    assert "boom" in dialog.status.text()


def test_workspace_loads_in_a_worker_and_a_service_account_opens_the_second_tab(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(rbac_data=lambda: {**data_of(cluster()), "missing": []}), "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_rbac(subject=("ServiceAccount", "app", "shop"))
    qtbot.waitUntil(lambda: dialog.perm_table.rowCount() > 0, timeout=5000)
    assert dialog.tabs.currentIndex() == 1 and dialog.subject_namespace.text() == "shop"
    assert ws.show_rbac() is dialog
    ws.shutdown()
    qtbot.waitUntil(lambda: not ws._workers)


def test_service_accounts_have_the_menu_entry(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from klotse.ui.resource_table import ResourceTable
    for kind, expected in (("ServiceAccount", True), ("Role", False)):
        table = ResourceTable([("Name", "name")], kind)
        qtbot.addWidget(table)
        table.set_rows([SimpleNamespace(name="x", namespace="n", raw={"metadata": {}})])
        seen = []

        def read():
            menu = QApplication.activePopupWidget()
            seen.extend(a.text() for a in menu.actions())
            menu.close()
        QTimer.singleShot(200, read)
        table.view.selectRow(0)
        table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
        assert ("Effective permissions…" in seen) is expected
