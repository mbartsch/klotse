"""RoleBinding / ClusterRoleBinding form editor, the service-account directory and the routing from the Edit action."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row
from klotse.ui import binding_editor
from klotse.ui.account_directory import AccountDirectory
from klotse.ui.binding_editor import (BINDING_HELP, BindingEditDialog, binding_warnings, care_warnings,
                                      normalize_subject, subject_notes, subject_warnings, unique_subjects,
                                      validate_subjects)
from klotse.ui.workspace import ClusterWorkspace

RBAC = "rbac.authorization.k8s.io"
ANA = {"kind": "User", "apiGroup": RBAC, "name": "ana"}
CI = {"kind": "ServiceAccount", "name": "ci", "namespace": "build"}
RAW = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
       "metadata": {"name": "b", "namespace": "dev", "labels": {"keep": "me"}, "resourceVersion": "9"},
       "roleRef": {"apiGroup": RBAC, "kind": "ClusterRole", "name": "view"}, "subjects": [ANA, CI]}


def sync_run(fn, on_result, *args, on_error=None):
    try:
        result = fn(*args)
    except Exception as e:
        (on_error or (lambda m: None))(str(e))
        return
    on_result(result)


class Service:
    def __init__(self, accounts, fail=None):
        self.accounts, self.fail = accounts, fail

    def list_service_account_names(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return self.accounts


def directory(*accounts, started=True):
    d = AccountDirectory(Service(list(accounts)), sync_run)
    if started:
        d.start()
    return d


ACCOUNTS = [("build", "ci"), ("build", "deployer"), ("dev", "default"), ("kube-system", "coredns")]


def make(qtbot, raw=RAW, kind="RoleBinding", accounts=ACCOUNTS):
    dlg = BindingEditDialog(kind, raw, directory(*accounts))
    qtbot.addWidget(dlg)
    return dlg


# ---- pure helpers ---------------------------------------------------------------
def test_normalize_subject_follows_the_api_shape():
    assert normalize_subject({"kind": "User", "name": " ana "}) == ANA
    assert normalize_subject({"kind": "Group", "name": "ops", "namespace": "x", "apiGroup": "wrong"}) == \
        {"kind": "Group", "name": "ops", "apiGroup": RBAC}  # no namespace on a group, and the right apiGroup
    assert normalize_subject({"kind": "ServiceAccount", "name": "ci", "namespace": "build", "apiGroup": RBAC}) == CI
    assert normalize_subject({"kind": "ServiceAccount", "name": "ci"})["namespace"] == ""
    assert normalize_subject(None) == {"kind": "", "name": ""}


def test_unique_subjects_and_validation():
    assert unique_subjects([ANA, dict(ANA), CI, {**CI, "apiGroup": ""}]) == [ANA, CI]
    assert validate_subjects([ANA, CI]) == []
    problems = validate_subjects([{"kind": "Robot", "name": "x"}, {"kind": "User", "name": ""},
                                  {"kind": "ServiceAccount", "name": "a"}])
    assert len(problems) == 3 and "kind must be one of" in problems[0] and "enter a name" in problems[1]
    assert "needs a namespace" in problems[2]


def test_notes_and_warnings_for_subjects():
    assert subject_notes([{"kind": "User", "name": "system:serviceaccount:build:ci"}])[0].endswith("choose kind ServiceAccount instead")
    assert subject_notes([ANA, CI]) == []
    assert subject_warnings([ANA, CI]) == []
    each = lambda kind, name: subject_warnings([{"kind": kind, "name": name}])
    assert "without logging in" in each("Group", "system:unauthenticated")[0]
    assert "without logging in" in each("User", "system:anonymous")[0]
    assert "every authenticated user" in each("Group", "system:authenticated")[0]
    assert "superusers" in each("Group", "system:masters")[0]
    assert "in the cluster" in each("Group", "system:serviceaccounts")[0]
    assert "in namespace build" in each("Group", "system:serviceaccounts:build")[0]
    assert each("User", "system:masters") == []  # only the group is special


def test_binding_warnings_default_bindings_and_cluster_admin():
    assert binding_warnings(RAW) == []
    system = {"metadata": {"name": "system:node", "labels": {"kubernetes.io/bootstrapping": "rbac-defaults"}},
              "roleRef": {"kind": "ClusterRole", "name": "system:node"}}
    assert "default subjects are added back" in binding_warnings(system)[0]
    off = {"metadata": {**system["metadata"], "annotations": {"rbac.authorization.kubernetes.io/autoupdate": "false"}}}
    assert "switched off" in binding_warnings(off)[0]
    admin = {"kind": "ClusterRoleBinding", "metadata": {"name": "root"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"}}
    assert "anything in the whole cluster" in binding_warnings(admin)[0]
    scoped = {"kind": "RoleBinding", "metadata": {"name": "x", "namespace": "dev"}, "roleRef": {"name": "cluster-admin"}}
    assert "in namespace dev" in binding_warnings(scoped)[0]


def test_care_warnings_covers_roles_bindings_and_nothing_else():
    assert care_warnings("Role", {"metadata": {"name": "system:x"}}) and care_warnings("ClusterRoleBinding", {
        "metadata": {"name": "system:x"}})
    assert care_warnings("ConfigMap", {"metadata": {"name": "system:x"}}) == []


# ---- the account directory ------------------------------------------------------
def test_directory_loads_sorted_unique_and_searches_with_a_cap(qtbot):
    d = directory(("b", "y"), ("a", "x"), ("a", "x"))
    assert d.entries == [("a", "x"), ("b", "y")] and d.state == "done" and d.status_text() == "2 service accounts"
    assert d.search("b/y") == ([("b", "y")], False) and d.search("X")[0] == [("a", "x")]
    big = directory(*[("ns", f"sa{i}") for i in range(500)])
    hits, more = big.search("", limit=200)
    assert len(hits) == 200 and more
    assert big.search("sa49", limit=200)[0] and big.search("nothing") == ([], False)


def test_directory_reports_failures_and_reloads(qtbot):
    d = AccountDirectory(Service([], fail="forbidden"), sync_run)
    with qtbot.waitSignal(d.finished, timeout=2000):
        d.start()
    assert d.state == "failed" and "forbidden" in d.status_text()
    d.service = Service([("a", "b")])
    d.start()
    assert d.state == "done" and d.entries == [("a", "b")]
    d.service = Service([("c", "d")])
    d.start()  # already loaded: not asked again
    assert d.entries == [("a", "b")]
    d.start(force=True)
    assert d.entries == [("c", "d")]


def test_service_lists_account_names_from_every_namespace():
    svc = KubeService()
    items = [SimpleNamespace(metadata=SimpleNamespace(namespace="n", name="a"))]
    svc._core = SimpleNamespace(list_service_account_for_all_namespaces=lambda **kw: SimpleNamespace(
        items=items, metadata=SimpleNamespace(_continue=None)))
    assert svc.list_service_account_names() == [("n", "a")]


# ---- the dialog -----------------------------------------------------------------
def kinds(dlg):
    return [dlg.table.cellWidget(r, 0).currentText() for r in range(dlg.table.rowCount())]


def test_dialog_shows_role_ref_read_only_and_the_subjects(qtbot):
    dlg = make(qtbot)
    assert "ClusterRole" in dlg.role_label.text() and "view" in dlg.role_label.text() and "dev" in dlg.role_label.text()
    assert "cannot be changed" in dlg.role_label.text()
    assert kinds(dlg) == ["User", "ServiceAccount"]
    assert [dlg.table.item(r, 1).text() for r in range(2)] == ["ana", "ci"]
    assert dlg.table.item(0, 2).flags() == Qt.NoItemFlags and dlg.table.item(1, 2).text() == "build"
    assert dlg.banner.isHidden()


def test_role_ref_text_from_the_cluster_is_escaped(qtbot):
    raw = {**RAW, "roleRef": {"kind": "ClusterRole", "name": "<b>evil</b>"}, "subjects": []}
    dlg = make(qtbot, raw)
    assert "&lt;b&gt;evil" in dlg.role_label.text() and "<b>evil" not in dlg.role_label.text()


def test_add_user_and_group_by_name_without_duplicates(qtbot):
    dlg = make(qtbot)
    dlg.name_edit.setText("bob")
    dlg._add_named()
    dlg.add_kind.setCurrentIndex(1)
    dlg.name_edit.setText("ops")
    dlg._add_named()
    dlg.add_kind.setCurrentIndex(0)
    dlg.name_edit.setText("ana")  # already there
    dlg._add_named()
    assert [(s["kind"], s["name"]) for s in dlg.subjects] == [("User", "ana"), ("ServiceAccount", "ci"),
                                                              ("User", "bob"), ("Group", "ops")]
    assert dlg.name_edit.text() == "" and dlg.table.rowCount() == 4


def test_the_group_name_box_offers_well_known_groups_only_for_groups(qtbot):
    dlg = make(qtbot)
    dlg.add_kind.setCurrentIndex(1)
    assert dlg.name_edit.completer() is dlg.name_completer and dlg.pages.currentIndex() == 0
    dlg.add_kind.setCurrentIndex(0)
    assert dlg.name_edit.completer() is None
    dlg.add_kind.setCurrentIndex(2)
    assert dlg.pages.currentIndex() == 1


def test_pick_service_accounts_from_the_cluster_list(qtbot):
    dlg = make(qtbot)
    dlg.add_kind.setCurrentIndex(2)
    dlg.accounts.query.setText("dev default")
    dlg.accounts.refresh()
    assert dlg.accounts.results.count() == 1 and dlg.accounts.chosen.isHidden()  # a direct picker has no chosen list
    dlg.accounts.results.item(0).setSelected(True)
    dlg.accounts._add_selected()
    assert {"kind": "ServiceAccount", "name": "default", "namespace": "dev"} in dlg.subjects
    assert dlg.accounts.values() == []  # a direct picker keeps nothing: the dialog's table is the list
    dlg.accounts.results.item(0).setSelected(True)
    dlg.accounts._add_selected()  # picking it again adds nothing
    assert len(dlg.subjects) == 3


def test_add_a_service_account_by_typing_namespace_and_name(qtbot):
    dlg = make(qtbot)
    dlg.accounts.query.setText("later/robot")  # not in the list: the account may not exist yet
    dlg._add_typed()
    assert dlg.subjects[-1] == {"kind": "ServiceAccount", "name": "robot", "namespace": "later"}
    dlg.accounts.query.setText("bare")  # a RoleBinding's own namespace is the default
    dlg._add_typed()
    assert dlg.subjects[-1]["namespace"] == "dev" and dlg.subjects[-1]["name"] == "bare"
    clusterwide = make(qtbot, {**RAW, "kind": "ClusterRoleBinding", "metadata": {"name": "c"}}, "ClusterRoleBinding")
    clusterwide.accounts.query.setText("bare")
    clusterwide._add_typed()  # no namespace to fall back on
    assert len(clusterwide.subjects) == 2 and "namespace/name" in clusterwide.accounts_status.text()


def test_edit_cells_and_change_the_kind_in_place(qtbot):
    dlg = make(qtbot)
    dlg.table.item(0, 1).setText("  anna ")
    assert dlg.subjects[0]["name"] == "anna"
    dlg.table.item(1, 2).setText("prod")
    assert dlg.subjects[1]["namespace"] == "prod"
    dlg.table.cellWidget(1, 0).setCurrentText("Group")  # becomes a group: no namespace any more
    assert dlg.subjects[1] == {"kind": "Group", "name": "ci", "apiGroup": RBAC}
    assert dlg.table.item(1, 2).flags() == Qt.NoItemFlags and dlg.table.item(1, 2).text() == ""
    dlg.table.cellWidget(1, 0).setCurrentText("ServiceAccount")  # and back: it starts in the binding's namespace
    assert dlg.subjects[1]["namespace"] == "dev" and dlg.table.item(1, 2).flags() != Qt.NoItemFlags


def test_remove_selected_subjects(qtbot):
    dlg = make(qtbot)
    dlg.table.selectRow(0)
    dlg._remove_selected()
    assert [s["name"] for s in dlg.subjects] == ["ci"] and dlg.table.rowCount() == 1
    dlg.table.selectAll()
    dlg._remove_selected()
    assert dlg.subjects == []
    dlg._remove_selected()  # nothing selected: nothing happens


def test_save_changes_only_the_subjects(qtbot):
    dlg = make(qtbot)
    dlg.name_edit.setText("bob")
    dlg._add_named()
    dlg._save()
    body = dlg.result_body
    assert dlg.result() == QDialog.Accepted
    assert body["roleRef"] == RAW["roleRef"] and body["metadata"] == RAW["metadata"]
    assert body["subjects"] == [ANA, CI, {"kind": "User", "name": "bob", "apiGroup": RBAC}]
    assert RAW["subjects"] == [ANA, CI]  # the row's own manifest is untouched


def test_saving_an_untouched_binding_changes_nothing(qtbot):
    dlg = make(qtbot)
    dlg._save()
    assert dlg.result_body == RAW


def test_removing_every_subject_drops_the_field(qtbot):
    dlg = make(qtbot)
    dlg.table.selectAll()
    dlg._remove_selected()
    dlg._save()
    assert "subjects" not in dlg.result_body and dlg.result_body["roleRef"] == RAW["roleRef"]


def test_save_is_refused_until_the_subjects_are_valid(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = make(qtbot)
    dlg.table.item(1, 2).setText("")
    dlg._save()
    assert dlg.result_body is None and "Subject 2: a service account needs a namespace" in shown[0]
    assert "needs a namespace" in dlg.hint.text()


def test_adding_a_risky_subject_asks_but_an_existing_one_does_not(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    existing = {**RAW, "subjects": [{"kind": "Group", "name": "system:authenticated", "apiGroup": RBAC}]}
    kept = make(qtbot, existing)
    kept._save()
    assert kept.result_body is not None and not asked  # it was already there: no nagging
    dlg = make(qtbot)
    dlg.add_kind.setCurrentIndex(1)
    dlg.name_edit.setText("system:unauthenticated")
    dlg._add_named()
    dlg._save()
    assert dlg.result_body is None and "without logging in" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dlg._save()
    assert {"kind": "Group", "name": "system:unauthenticated", "apiGroup": RBAC} in dlg.result_body["subjects"]


def test_a_flagged_binding_shows_a_banner_and_asks_before_saving(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    raw = {"metadata": {"name": "root"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"}, "subjects": [ANA]}
    dlg = make(qtbot, raw, "ClusterRoleBinding")
    dlg.show()
    assert not dlg.banner.isHidden() and "cluster-admin" in dlg.banner.text()
    dlg._save()
    assert dlg.result_body is None and "anything in the whole cluster" in asked[0]


def test_help_button_explains_subjects_and_the_fixed_role(qtbot, monkeypatch):
    dlg = make(qtbot)
    tip = dlg.help_button.toolTip()
    assert tip == BINDING_HELP and tip.startswith("<")
    for phrase in ("ServiceAccount", "system:serviceaccount:", "system:authenticated", "system:unauthenticated",
                   "cannot be changed", "delete the binding"):
        assert phrase in tip
    shown = []
    monkeypatch.setattr(binding_editor.QToolTip, "showText", lambda pos, text, widget=None: shown.append((text, widget)))
    dlg.help_button.click()
    assert shown == [(tip, dlg.help_button)]


def test_edit_as_yaml_rejects_and_flags_it(qtbot):
    dlg = make(qtbot)
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.result_body is None and dlg.result() == QDialog.Rejected


def test_the_list_follows_the_directory_while_it_loads(qtbot):
    d = directory(started=False)
    dlg = BindingEditDialog("RoleBinding", RAW, d)
    qtbot.addWidget(dlg)
    assert dlg.accounts_status.text() == "0 service accounts"  # nothing loaded yet
    d.service = Service(ACCOUNTS)
    with qtbot.waitSignal(d.finished, timeout=2000):
        d.start()
    qtbot.waitUntil(lambda: "4 service accounts" in dlg.accounts_status.text(), timeout=2000)


# ---- workspace routing ----------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_workspace_edits_bindings_with_the_form_and_saves_the_result(qtbot, monkeypatch):
    saved, seen = [], {}
    ws = make_ws(qtbot, replace=lambda kind, body: saved.append((kind, body)), list_service_account_names=lambda: [])
    ws.refresh = lambda: None

    class FakeForm:
        def __init__(self, kind, raw, directory, parent=None):
            seen.update(kind=kind, directory=directory)
            self.result_body, self.wants_yaml = {"metadata": {"name": "b"}}, False

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.BindingEditDialog", FakeForm)
    ws.edit_resource("RoleBinding", Row("b", "dev", RAW))
    qtbot.waitUntil(lambda: bool(saved), timeout=3000)
    assert saved == [("RoleBinding", {"metadata": {"name": "b"}})] and seen["kind"] == "RoleBinding"
    assert ws.account_directory() is seen["directory"]  # one directory per workspace


def test_edit_as_yaml_from_the_binding_form_opens_the_yaml_editor_with_the_note(qtbot, monkeypatch):
    ws = make_ws(qtbot, list_service_account_names=lambda: [])
    opened = []

    class Form:
        def __init__(self, *a, **k):
            self.wants_yaml, self.result_body = True, None

        def exec(self):
            return QDialog.Rejected

        def deleteLater(self):
            pass

    class YamlDialog:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note=""):
            opened.append((title, note))

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.BindingEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", YamlDialog)
    root = {**RAW, "metadata": {"name": "root"}, "roleRef": {"kind": "ClusterRole", "name": "cluster-admin"}}
    ws.edit_resource("ClusterRoleBinding", Row("root", "", root))
    assert opened[0][0] == "Edit ClusterRoleBinding root" and "cluster-admin" in opened[0][1]


def test_deleting_bindings_warns_for_cluster_admin_and_system_ones(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    ws = make_ws(qtbot, delete_resource=lambda *a: pytest.fail("must not delete"))
    root = {"kind": "ClusterRoleBinding", "metadata": {"name": "root", "uid": "u"}, "roleRef": {"name": "cluster-admin"}}
    ws.delete_resource("ClusterRoleBinding", Row("root", "", root))
    assert "Its subjects lose the access" in asked[0] and "grants cluster-admin" in asked[0]
    ws.bulk_action("RoleBinding", "delete", [Row("a", "n", {"metadata": {"name": "a", "uid": "1"}}),
                                             Row("system:x", "n", {"metadata": {"name": "system:x", "uid": "2"}})])
    assert "1 of them are default" in asked[1] and "system:x" in asked[1]
