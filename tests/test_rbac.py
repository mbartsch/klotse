from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from kubernetes import client

from klotse.ui import theme
from klotse.k8s.client import KubeService
from klotse.k8s.resources import (DELETABLE_KINDS, SPEC_BY_KEY, SPEC_BY_KIND, map_binding, map_clusterrole, map_role,
                                  map_serviceaccount)
from klotse.ui.navigator import VIEWS
from klotse.ui.resource_table import ResourceTable

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def meta(name, namespace=None):
    return client.V1ObjectMeta(name=name, namespace=namespace, creation_timestamp=NOW, uid="u-" + name)


def rule(verbs, resources=None, groups=None, urls=None, names=None):
    url_arg = "non_resource_urls" if "non_resource_urls" in getattr(client.V1PolicyRule, "model_fields", {}) \
        else "non_resource_ur_ls"  # the client renamed it
    return client.V1PolicyRule(verbs=verbs, resources=resources, api_groups=groups, resource_names=names,
                               **{url_arg: urls})


def test_role_rows_are_searchable_text():
    role = client.V1Role(metadata=meta("reader", "dev"), rules=[
        rule(["get", "list"], ["pods", "secrets"], [""]), rule(["get"], ["deployments"], ["apps"], names=["web"])])
    row = map_role(role)
    assert row.rule_count == 2 and row.namespace == "dev"
    assert "pods: get,list" not in row.rules  # one line per rule lists resources before verbs
    assert "pods,secrets: get,list" in row.rules
    assert "apps/deployments,names=web: get" in row.rules


def test_clusterrole_nonresource_urls_and_aggregation():
    role = client.V1ClusterRole(metadata=meta("view"), rules=[rule(["get"], urls=["/healthz"])],
                                aggregation_rule=client.V1AggregationRule())
    row = map_clusterrole(role)
    assert row.aggregated == "yes" and "/healthz: get" in row.rules
    assert map_clusterrole(client.V1ClusterRole(metadata=meta("x"))).rule_count == 0


def test_binding_shows_role_and_subjects():
    subjects = [client.RbacV1Subject(kind="User", name="ana", api_group="rbac.authorization.k8s.io"),
                client.RbacV1Subject(kind="ServiceAccount", name="ci", namespace="build")]
    b = client.V1RoleBinding(metadata=meta("b", "dev"), subjects=subjects,
                             role_ref=client.V1RoleRef(kind="ClusterRole", name="view", api_group="rbac.authorization.k8s.io"))
    row = map_binding(b)
    assert row.role == "ClusterRole/view" and row.subject_count == 2
    assert row.subjects == "User:ana, ServiceAccount:build/ci"
    assert map_binding(client.V1ClusterRoleBinding(metadata=meta("e"), role_ref=b.role_ref)).subjects == ""


def test_serviceaccount_automount_unknown_is_blank():
    assert map_serviceaccount(client.V1ServiceAccount(metadata=meta("d", "n"))).automount == ""
    assert map_serviceaccount(client.V1ServiceAccount(metadata=meta("d", "n"), automount_service_account_token=False)).automount == "False"


def test_registry_and_navigator():
    keys = ["serviceaccounts", "roles", "rolebindings", "clusterroles", "clusterrolebindings"]
    for key in keys:
        spec = SPEC_BY_KEY[key]
        assert spec.editable and spec.deletable and SPEC_BY_KIND[spec.kind] is spec
    assert not SPEC_BY_KEY["clusterroles"].namespaced and SPEC_BY_KEY["roles"].namespaced
    group = next(v for v in VIEWS if v[0] == "Access Control")
    assert [k for k, _ in group[1]] == keys
    assert "Secret" not in DELETABLE_KINDS and "Role" in DELETABLE_KINDS


class FakeApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("delete_"):
            return lambda *a, **kw: self.calls.append((name, a, kw))
        raise AttributeError(name)


def service_with(api):
    svc = KubeService()
    svc._apis = {"rbac": api, "core": api}
    return svc


def test_delete_uses_uid_precondition_and_right_method():
    api = FakeApi()
    svc = service_with(api)
    svc.delete_resource("Role", "reader", "dev", "uid-1")
    svc.delete_resource("ClusterRoleBinding", "crb", "", "uid-2")
    (n1, a1, kw1), (n2, a2, kw2) = api.calls
    assert (n1, a1) == ("delete_namespaced_role", ("reader", "dev"))
    assert (n2, a2) == ("delete_cluster_role_binding", ("crb",))
    assert kw1["body"].preconditions.uid == "uid-1" and kw2["body"].preconditions.uid == "uid-2"


def test_delete_refuses_other_kinds_and_missing_uid():
    svc = service_with(FakeApi())
    with pytest.raises(ValueError):
        svc.delete_resource("Secret", "s", "n", "u")
    with pytest.raises(ValueError):
        svc.delete_resource("Role", "r", "n", "")


def labels(menu):
    return [a.text() for a in menu.actions()]


def test_table_offers_delete_only_for_rbac_kinds(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMenu
    shown = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a: shown.append(labels(self)))
    spec = SPEC_BY_KEY["roles"]
    table = ResourceTable(spec.columns, spec.kind, editable=spec.editable)
    qtbot.addWidget(table)
    table.set_rows([map_role(client.V1Role(metadata=meta("a", "n"), rules=[])),
                    map_role(client.V1Role(metadata=meta("b", "n"), rules=[]))])
    table.show()
    table.view.selectAll()
    table._bulk_menu(menu := QMenu(), table.selected_rows())
    assert "Delete 2 Roles…" in labels(menu)
    other = ResourceTable(SPEC_BY_KEY["configmaps"].columns, "ConfigMap")
    qtbot.addWidget(other)
    other._bulk_menu(menu2 := QMenu(), [])
    assert menu2.isEmpty()


# ---- formatted Details view ------------------------------------------------
from klotse.ui import rbac_view
from klotse.ui.detail_panel import DetailPanel
from klotse.k8s.util import to_dict


def test_rows_know_their_rbac_kind():
    assert map_role(client.V1Role(metadata=meta("a", "n"))).rbac_kind == "Role"
    assert map_clusterrole(client.V1ClusterRole(metadata=meta("a"))).rbac_kind == "ClusterRole"
    ref = client.V1RoleRef(kind="Role", name="r", api_group="rbac.authorization.k8s.io")
    assert map_binding(client.V1RoleBinding(metadata=meta("b", "n"), role_ref=ref)).rbac_kind == "RoleBinding"
    assert map_binding(client.V1ClusterRoleBinding(metadata=meta("b"), role_ref=ref)).rbac_kind == "ClusterRoleBinding"
    assert map_serviceaccount(client.V1ServiceAccount(metadata=meta("s", "n"))).rbac_kind == "ServiceAccount"


def test_render_role_lists_rules_and_flags_risky_ones():
    role = client.V1Role(metadata=meta("r", "dev"), rules=[
        rule(["get"], ["pods"], [""]), rule(["*"], ["secrets"], [""], names=["db"])])
    html = rbac_view.render("Role", to_dict(role))
    assert "pods" in html and "db" in html and "(core)" in html
    assert html.count(theme.WARN) >= 3  # the '*' verb, 'secrets' and the legend; plain get/pods are not flagged
    assert "No rules" in rbac_view.render("Role", to_dict(client.V1Role(metadata=meta("e", "dev"))))


def test_render_binding_and_serviceaccount():
    ref = client.V1RoleRef(kind="ClusterRole", name="view", api_group="rbac.authorization.k8s.io")
    b = client.V1RoleBinding(metadata=meta("b", "dev"), role_ref=ref, subjects=[
        client.RbacV1Subject(kind="ServiceAccount", name="ci", namespace="build")])
    html = rbac_view.render("RoleBinding", to_dict(b))
    assert "view" in html and "ci" in html and "build" in html
    assert "No subjects" in rbac_view.render("RoleBinding", to_dict(client.V1RoleBinding(metadata=meta("b", "dev"), role_ref=ref)))
    sa = client.V1ServiceAccount(metadata=meta("s", "dev"), secrets=[client.V1ObjectReference(name="tok")])
    assert "tok" in rbac_view.render("ServiceAccount", to_dict(sa))


def test_render_escapes_cluster_text():
    evil = "<script>alert(1)</script><img src=x>"
    role = client.V1Role(metadata=client.V1ObjectMeta(name=evil, namespace="n", labels={evil: evil}),
                         rules=[rule([evil], [evil], [evil], names=[evil])])
    html = rbac_view.render("Role", to_dict(role))
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html


def test_render_rejects_other_kinds():
    with pytest.raises(ValueError):
        rbac_view.render("Pod", {})


def test_detail_panel_toggles_between_formatted_and_yaml(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    row = map_role(client.V1Role(metadata=meta("reader", "dev"), rules=[rule(["get"], ["pods"], [""])]))
    panel.show_row(row)
    assert panel.view_bar.isVisible() and panel.formatted.isVisible() and not panel.editor.isVisible()
    assert "reader" in panel.formatted.toPlainText()
    panel.yaml_button.click()
    assert panel.editor.isVisible() and not panel.formatted.isVisible()
    assert "kind" in panel.toPlainText() or "name: reader" in panel.toPlainText()
    panel.show_row(map_role(client.V1Role(metadata=meta("other", "dev"))))  # the choice sticks
    assert panel.editor.isVisible()
    plain = SimpleNamespace(raw={"metadata": {"name": "cm"}})  # not an RBAC row: no toggle, YAML only
    panel.show_row(plain)
    assert not panel.view_bar.isVisible() and panel.editor.isVisible()
    panel.show_row(row)  # back to an RBAC row: still YAML because the user picked it
    assert panel.editor.isVisible()


def test_details_show_a_readable_time_and_no_white_rule():
    from klotse.ui.rbac_view import _when
    assert _when("2026-01-01T00:00:00+00:00") == "2026-01-01 00:00 UTC"
    assert _when("2026-03-04T10:30:00Z") == "2026-03-04 10:30 UTC"
    assert _when("2026-03-04T12:30:00+02:00") == "2026-03-04 10:30 UTC"
    assert _when("not a date") == "not a date"
    role = client.V1Role(metadata=meta("r", "dev"))
    html = rbac_view.render("Role", {**to_dict(role)})
    assert "<hr" not in html and "00:00 UTC" in html  # Qt draws <hr> white on every theme


def test_formatted_view_has_no_uid_and_lists_labels_before_rules():
    role = client.V1Role(metadata=client.V1ObjectMeta(name="r", namespace="dev", uid="u-secret-id", labels={"a": "b"},
                                                      annotations={"n": "m"}), rules=[rule(["get"], ["pods"], [""])])
    html = rbac_view.render("Role", to_dict(role))
    assert "UID" not in html and "u-secret-id" not in html  # still in the YAML view
    assert html.index("Labels") < html.index("Annotations") < html.index("Rules")
    ref = client.V1RoleRef(kind="Role", name="r", api_group="rbac.authorization.k8s.io")
    binding = client.V1RoleBinding(metadata=client.V1ObjectMeta(name="b", namespace="dev", labels={"a": "b"}),
                                   role_ref=ref, subjects=[client.RbacV1Subject(kind="User", name="u")])
    html = rbac_view.render("RoleBinding", to_dict(binding))
    assert html.index("Labels") < html.index("Grants role")
