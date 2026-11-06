"""View > My permissions…: the user's own rules in a namespace and a Can I? check."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog

from klotse.k8s import client as k8s_client
from klotse.k8s.client import KubeService
from klotse.ui.permissions_dialog import PermissionsDialog, rule_rows, verdict
from klotse.ui.workspace import ClusterWorkspace

PERMS = {"resources": [{"verbs": ["get", "list"], "groups": ["", "apps"], "resources": ["pods", "deployments"], "names": []},
                       {"verbs": ["*"], "groups": ["*"], "resources": ["*"], "names": ["only-this"]}],
         "urls": [{"verbs": ["get"], "urls": ["/healthz", "/version"]}], "incomplete": False, "error": ""}


# ---- pure helpers ------------------------------------------------------------------------------------------------
def test_rows_show_resources_first_then_urls_and_name_the_core_group():
    rows = rule_rows(PERMS)
    assert rows[0] == ("resource", "pods, deployments", "(core), apps", "get, list", "")
    assert rows[1] == ("resource", "*", "*", "*", "only-this")
    assert rows[2] == ("url", "/healthz, /version", "", "get", "")
    assert rule_rows({}) == []


def test_the_verdict_uses_a_symbol_and_the_word():
    assert verdict(True) == "✔ Allowed" and verdict(False, "RBAC: no rule") == "✖ Denied — RBAC: no rule"


# ---- the service -------------------------------------------------------------------------------------------------
class FakeAuth:
    def __init__(self, status):
        self.status, self.seen = status, []

    def create_self_subject_rules_review(self, body, **kw):
        self.seen.append(body)
        return SimpleNamespace(status=self.status)

    create_self_subject_access_review = create_self_subject_rules_review


def service(monkeypatch, status):
    auth = FakeAuth(status)
    monkeypatch.setattr(k8s_client.client, "AuthorizationV1Api", lambda api: auth)
    svc = object.__new__(KubeService)
    svc._api_client = object()
    return svc, auth


@pytest.mark.parametrize("spelling", ["non_resource_urls", "non_resource_ur_ls"])  # the client renamed it
def test_the_service_turns_the_rules_review_into_plain_data(monkeypatch, spelling):
    status = SimpleNamespace(resource_rules=[SimpleNamespace(verbs=["get"], api_groups=[""], resources=["pods"], resource_names=None)],
                             non_resource_rules=[SimpleNamespace(verbs=["get"], **{spelling: ["/healthz"]})],
                             incomplete=True, evaluation_error=None)
    svc, auth = service(monkeypatch, status)
    got = svc.my_permissions("shop")
    assert auth.seen[0].spec.namespace == "shop"
    assert got == {"resources": [{"verbs": ["get"], "groups": [""], "resources": ["pods"], "names": []}],
                   "urls": [{"verbs": ["get"], "urls": ["/healthz"]}], "incomplete": True, "error": ""}


def test_can_i_splits_subresources_and_reports_the_reason(monkeypatch):
    svc, auth = service(monkeypatch, SimpleNamespace(allowed=False, reason="no rule", evaluation_error=None))
    assert svc.can_i("get", "", "pods/log", "shop", "web-1") == (False, "no rule")
    attrs = auth.seen[0].spec.resource_attributes
    assert (attrs.verb, attrs.resource, attrs.subresource, attrs.namespace, attrs.name, attrs.group) == (
        "get", "pods", "log", "shop", "web-1", "")
    svc, _ = service(monkeypatch, SimpleNamespace(allowed=True, reason="", evaluation_error=None))
    assert svc.can_i("list", "apps", "deployments", "shop") == (True, "")


# ---- the dialog --------------------------------------------------------------------------------------------------
def dialog(qtbot, perms=PERMS):
    d = PermissionsDialog("ctx", "shop", perms)
    qtbot.addWidget(d)
    return d


def test_the_dialog_lists_the_rules_and_filters_them(qtbot):
    d = dialog(qtbot)
    d.show()
    assert d.table.rowCount() == 3 and not d.note.isVisible()
    d.filter.setText("healthz")
    assert [d.table.isRowHidden(r) for r in range(3)] == [True, True, False]
    d.filter.setText("get apps")
    assert [d.table.isRowHidden(r) for r in range(3)] == [False, True, True]


def test_an_incomplete_answer_or_error_is_explained(qtbot):
    d = dialog(qtbot, {**PERMS, "incomplete": True, "error": "webhook down"})
    d.show()
    assert "incomplete" in d.note.text() and "webhook down" in d.note.text() and d.note.isVisible()
    empty = dialog(qtbot, {})
    assert "no rules" in empty.note.text()


def test_the_check_needs_a_resource_and_emits_what_was_typed(qtbot):
    d = dialog(qtbot)
    asked = []
    d.check_requested.connect(lambda *a: asked.append(a))
    d.check_button.click()
    assert asked == [] and "resource first" in d.result.text()
    d.verb.setCurrentText("delete")
    d.resource.setText(" pods/log ")
    d.group.setText("apps")
    d.name.setText("web-1")
    d.check_button.click()
    assert asked == [("delete", "apps", "pods/log", "web-1")]
    d.show_check(False, "no rule")
    assert d.result.text() == "✖ Denied — no rule"
    d.show_check(error="timeout")
    assert d.result.text() == "Cannot check: timeout"


# ---- the workspace -----------------------------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_the_workspace_reads_the_permissions_of_the_namespace_in_front(qtbot, monkeypatch):
    seen = []
    ws = make_ws(qtbot, my_permissions=lambda ns: seen.append(ns) or PERMS)
    shown = []
    monkeypatch.setattr(PermissionsDialog, "exec", lambda self: shown.append(self.table.rowCount()) or QDialog.Accepted)
    ws.show_permissions()
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert seen == ["default"] and shown == [3]


def test_all_namespaces_asks_about_default(qtbot, monkeypatch):
    seen = []
    ws = make_ws(qtbot, my_permissions=lambda ns: seen.append(ns) or PERMS)
    ws.ns_combo.setCurrentIndex(0)
    monkeypatch.setattr(PermissionsDialog, "exec", lambda self: QDialog.Accepted)
    ws.show_permissions()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen == ["default"]


def test_the_check_runs_in_the_background_and_answers_in_the_dialog(qtbot):
    calls = []
    ws = make_ws(qtbot, can_i=lambda *a: calls.append(a) or (True, "RBAC allowed"))
    d = ws.permissions_dialog("shop", PERMS)
    qtbot.addWidget(d)
    d.resource.setText("pods")
    d.check_button.click()
    qtbot.waitUntil(lambda: "Allowed" in d.result.text(), timeout=5000)
    assert calls == [("get", "", "pods", "shop", "")] and d.result.text() == "✔ Allowed — RBAC allowed"


def test_a_failing_check_is_shown_in_the_dialog(qtbot):
    def boom(*a):
        raise RuntimeError("forbidden")
    ws = make_ws(qtbot, can_i=boom)
    d = ws.permissions_dialog("shop", PERMS)
    qtbot.addWidget(d)
    d.resource.setText("pods")
    d.check_button.click()
    qtbot.waitUntil(lambda: "Cannot check" in d.result.text(), timeout=5000)
    assert "forbidden" in d.result.text()


# ---- the menu ----------------------------------------------------------------------------------------------------
def test_the_view_menu_entry_needs_a_cluster_in_front(qtbot, tmp_path):
    from PySide6.QtCore import QSettings
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService, open_active

    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    view = win.permissions_action.associatedObjects()[0]
    assert win.permissions_action in view.actions()
    view.aboutToShow.emit()
    assert not win.permissions_action.isEnabled() and win.show_permissions() is False
    open_active(win)
    asked = []
    win.workspace("ctx").show_permissions = lambda: asked.append(True)
    view.aboutToShow.emit()
    assert win.permissions_action.isEnabled() and win.show_permissions() is True and asked == [True]


def test_the_real_client_model_objects_are_understood(monkeypatch):
    """Built from the installed client's own classes, so a renamed attribute shows up here and not in front of a user."""
    status = k8s_client.client.V1SubjectRulesReviewStatus(
        incomplete=False,
        resource_rules=[k8s_client.client.V1ResourceRule(verbs=["get"], api_groups=[""], resources=["pods"])],
        non_resource_rules=[k8s_client.client.V1NonResourceRule(verbs=["get"], non_resource_urls=["/healthz"])])
    svc, _ = service(monkeypatch, status)
    got = svc.my_permissions("shop")
    assert got["urls"] == [{"verbs": ["get"], "urls": ["/healthz"]}]
    assert got["resources"] == [{"verbs": ["get"], "groups": [""], "resources": ["pods"], "names": []}]
