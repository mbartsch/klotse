"""Role / ClusterRole form editor: the API index, the discovery loader, the rule helpers and the dialog."""
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s.api_index import ApiIndex, ApiResource
from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row
from klotse.ui import role_editor
from klotse.ui.api_index_loader import ApiIndexLoader
from klotse.ui.role_editor import (RESULT_LIMIT, RoleEditDialog, grants_everything, normalize_rule, rule_summary,
                                   validate_rules)
from klotse.ui.workspace import ClusterWorkspace

CORE = [ApiResource("", "pods", "Pod", True, "v1"), ApiResource("", "pods/log", "Pod", True, "v1"),
        ApiResource("", "secrets", "Secret", True, "v1"), ApiResource("", "nodes", "Node", False, "v1")]
APPS = [ApiResource("apps", "deployments", "Deployment", True, "v1"), ApiResource("apps", "statefulsets", "StatefulSet", True, "v1")]
CERT = [ApiResource("cert-manager.io", "certificates", "Certificate", True, "v1"),
        ApiResource("cert-manager.io", "issuers", "Issuer", True, "v1")]


def index_with(*groups):
    index = ApiIndex()
    for g in groups:
        index.add(g)
    return index


# ---- ApiIndex ------------------------------------------------------------------
def test_index_search_matches_every_word_in_group_name_or_kind():
    index = index_with(CERT, CORE, APPS)
    assert [r.name for r in index.search("pod")[0]] == ["pods", "pods/log"]
    assert [r.name for r in index.search("cert issuer")[0]] == ["issuers"]  # words may hit different fields
    assert [r.name for r in index.search("StatefulSet")[0]] == ["statefulsets"]  # by kind
    assert [r.name for r in index.search("", groups=["apps"])[0]] == ["deployments", "statefulsets"]
    assert index.search("nothing-like-this") == ([], False)


def test_index_orders_core_first_and_ignores_duplicates():
    index = index_with(CERT, APPS, CORE)
    assert index.add(CORE) == 0 and len(index) == 8
    assert [r.group for r in index.search("")[0]][:4] == ["", "", "", ""]
    assert index.search_groups("")[0] == ["", "apps", "cert-manager.io"]
    assert index.search_groups("core")[0] == [""] and index.search_groups("cert")[0] == ["cert-manager.io"]


def test_index_limits_results_and_says_there_are_more():
    index = index_with([ApiResource("g", f"r{i}", "K", True) for i in range(50)])
    hits, more = index.search("", limit=10)
    assert len(hits) == 10 and more
    hits, more = index.search("r4", limit=10)  # r4, r40..r49 = 11 matches
    assert len(hits) == 10 and more
    assert index.search("", limit=50) == (index.search("")[0], False)


def test_index_stays_fast_with_a_very_large_number_of_resources():
    index = ApiIndex()
    index.add([ApiResource(f"group{i // 5}.example.com", f"thing{i}s", f"Thing{i}", True) for i in range(200_000)])
    started = time.perf_counter()
    hits, more = index.search("thing199999")
    searched = time.perf_counter() - started
    started = time.perf_counter()
    index.search("", limit=RESULT_LIMIT)
    first_page = time.perf_counter() - started
    assert [r.name for r in hits] == ["thing199999s"] and not more
    assert searched < 1.5 and first_page < 0.5  # one linear pass, and the empty query stops after a page


# ---- discovery and loader ------------------------------------------------------
def test_service_discovery_reads_core_and_group_resources():
    found = lambda *names: SimpleNamespace(resources=[SimpleNamespace(name=n, kind=n.title(), namespaced=True) for n in names])
    svc = KubeService()
    svc._core = SimpleNamespace(get_api_resources=lambda **kw: found("pods", "pods/log"))
    svc._custom = SimpleNamespace(get_api_resources=lambda g, v, **kw: found("widgets"))
    assert [(r.group, r.name, r.version) for r in svc.discovery_resources("", "v1")] == [("", "pods", "v1"), ("", "pods/log", "v1")]
    assert [(r.group, r.name, r.version) for r in svc.discovery_resources("x.io", "v2")] == [("x.io", "widgets", "v2")]


class Service:
    def __init__(self, groups, broken=()):
        self.groups, self.broken, self.calls = groups, set(broken), []

    def discovery_groups(self):
        return [(g, "v1") for g in self.groups]

    def discovery_resources(self, group, version):
        self.calls.append(group)
        if group in self.broken:
            raise RuntimeError("503")
        return [ApiResource(group, f"{group or 'core'}-thing", "Thing", True, version)]


def sync_run(fn, on_result, *args, on_error=None):
    try:
        result = fn(*args)
    except Exception as e:
        (on_error or (lambda m: None))(str(e))
        return
    on_result(result)


def test_loader_fetches_every_group_in_batches_and_skips_failures(qtbot):
    groups = [f"g{i}" for i in range(45)]
    service, index = Service(groups, broken=["g7"]), ApiIndex()
    loader = ApiIndexLoader(service, sync_run, index)
    seen = []
    loader.progress.connect(lambda done, total: seen.append((done, total)))
    with qtbot.waitSignal(loader.finished, timeout=2000):
        loader.start()
    assert loader.state == "done" and len(index) == 44 and loader.failed_groups == 1
    assert seen[0] == (0, 45) and seen[-1] == (45, 45)
    assert "44 resources" in loader.status_text() and "1 group(s)" in loader.status_text()
    loader.start()  # already loaded: nothing is fetched again
    assert len(service.calls) == 45


def test_loader_reports_a_failed_group_list(qtbot):
    service = Service([])
    service.discovery_groups = lambda: (_ for _ in ()).throw(RuntimeError("forbidden"))
    loader = ApiIndexLoader(service, sync_run, ApiIndex())
    with qtbot.waitSignal(loader.finished, timeout=2000):
        loader.start()
    assert loader.state == "failed" and "forbidden" in loader.status_text()


def test_loader_reload_clears_the_index(qtbot):
    service, index = Service(["a"]), ApiIndex()
    loader = ApiIndexLoader(service, sync_run, index)
    with qtbot.waitSignal(loader.finished, timeout=2000):
        loader.start()
    with qtbot.waitSignal(loader.finished, timeout=2000):
        loader.start(force=True)
    assert len(index) == 1 and len(service.calls) == 2


# ---- pure helpers --------------------------------------------------------------
def test_normalize_dedupes_keeps_core_group_and_drops_empty_fields():
    rule = {"apiGroups": ["", "", "apps"], "resources": [" pods ", "pods", ""], "verbs": ["get"], "resourceNames": [],
            "extra": "dropped"}
    assert normalize_rule(rule) == {"apiGroups": ["", "apps"], "resources": ["pods"], "verbs": ["get"]}
    assert normalize_rule(None) == {}


def test_validate_rules():
    ok = {"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}
    assert validate_rules([ok], cluster_scoped=False) == []
    assert any("verb" in p for p in validate_rules([{**ok, "verbs": []}], False))
    assert any("API group" in p for p in validate_rules([{"resources": ["pods"], "verbs": ["get"]}], False))
    assert any("resource" in p for p in validate_rules([{"apiGroups": [""], "verbs": ["get"]}], False))
    url = {"nonResourceURLs": ["/healthz"], "verbs": ["get"]}
    assert validate_rules([url], cluster_scoped=True) == []
    assert any("ClusterRole" in p for p in validate_rules([url], cluster_scoped=False))
    assert any("not both" in p for p in validate_rules([{**url, "apiGroups": [""]}], True))


def test_summary_and_cluster_admin_detection():
    assert rule_summary({"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "list"]}) == \
        "get,list  on  deployments.apps"
    assert rule_summary({"nonResourceURLs": ["/healthz"], "verbs": ["get"]}) == "get  on  /healthz"
    assert rule_summary({"apiGroups": ["a", "b"], "resources": ["x"], "verbs": ["get"]}).endswith("[2 API groups]")
    assert len(rule_summary({"apiGroups": [""], "resources": ["r" * 200], "verbs": ["get"]})) == 90
    star = {"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}
    assert grants_everything([star]) and not grants_everything([{**star, "verbs": ["get"]}])


# ---- the dialog ----------------------------------------------------------------
def loaded(*groups):
    index = index_with(*groups)
    loader = ApiIndexLoader(Service([]), sync_run, index)
    loader.state = "done"
    return loader


RAW = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
       "metadata": {"name": "r", "namespace": "dev", "labels": {"keep": "me"}, "resourceVersion": "7"},
       "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}]}


def dialog(qtbot, raw=RAW, kind="Role", *groups):
    dlg = RoleEditDialog(kind, raw, loaded(*(groups or (CORE, APPS, CERT))))
    qtbot.addWidget(dlg)
    return dlg


def pick(picker, *values):
    for i in range(picker.results.count()):
        item = picker.results.item(i)
        data = item.data(Qt.UserRole)
        item.setSelected(bool(data) and data[0] in values)
    picker._add_selected()


def test_dialog_shows_the_rules_and_loads_the_first_one(qtbot):
    dlg = dialog(qtbot)
    assert dlg.rule_list.count() == 1 and dlg.rule_list.item(0).text() == "get,list  on  pods"
    assert dlg.groups.values() == [""] and dlg.resources.values() == ["pods"]
    assert [v for v, b in dlg.verb_boxes.items() if b.isChecked()] == ["get", "list"]
    assert dlg.type_box.isHidden() and dlg.urls.isHidden()  # a Role has no URL rules


def test_picking_a_resource_from_another_group_adds_that_group(qtbot):
    dlg = dialog(qtbot)
    dlg.resources.query.setText("deploy")
    dlg.only_groups.setChecked(False)  # deployments are not in the rule's group yet
    dlg.resources.refresh()
    pick(dlg.resources, "deployments")
    assert dlg.resources.values() == ["pods", "deployments"] and dlg.groups.values() == ["", "apps"]
    assert dlg.rule_list.item(0).text().startswith("get,list  on  pods, deployments")


def test_resource_search_is_limited_to_the_chosen_groups_unless_switched_off(qtbot):
    dlg = dialog(qtbot)
    names = lambda: [dlg.resources.results.item(i).data(Qt.UserRole)[0] for i in range(dlg.resources.results.count())
                     if dlg.resources.results.item(i).data(Qt.UserRole)]
    dlg.resources.refresh()
    assert "pods" in names() and "deployments" not in names() and "*" in names()
    dlg.only_groups.setChecked(False)
    assert "deployments" in names() and "certificates" in names()


def test_search_results_are_capped_and_say_so(qtbot):
    many = [ApiResource("big.io", f"kind{i}s", f"Kind{i}", True) for i in range(RESULT_LIMIT + 50)]
    dlg = dialog(qtbot, RAW, "Role", many)
    dlg.groups.set_values(["big.io"])
    dlg.resources.refresh()
    real = [i for i in range(dlg.resources.results.count()) if dlg.resources.results.item(i).data(Qt.UserRole)]
    assert len(real) == RESULT_LIMIT + 1  # the page of results plus the '* (all resources)' entry
    assert "type to narrow" in dlg.resources.results.item(dlg.resources.results.count() - 1).text()


def test_verbs_all_excludes_the_single_verbs_and_unknown_verbs_survive(qtbot):
    raw = {**RAW, "rules": [{"apiGroups": ["policy"], "resources": ["podsecuritypolicies"], "verbs": ["use"]},
                            {"apiGroups": [""], "resources": ["pods"], "verbs": ["frobnicate"]}]}
    dlg = dialog(qtbot, raw)
    assert dlg.verb_boxes["use"].isChecked()
    dlg._select(1)
    assert dlg.verb_boxes["frobnicate"].isChecked()  # a verb the form does not list is still shown and kept
    dlg.verb_boxes["*"].setChecked(True)
    assert dlg.rules[1]["verbs"] == ["*"]
    dlg.verb_boxes["get"].setChecked(True)
    assert dlg.rules[1]["verbs"] == ["get"] and not dlg.verb_boxes["*"].isChecked()


def test_add_duplicate_move_and_remove_rules(qtbot):
    dlg = dialog(qtbot)
    dlg._add_rule()
    assert dlg.rule_list.count() == 2 and dlg.rules[1] == {"apiGroups": [""], "verbs": ["get", "list", "watch"]}
    dlg.names.query.setText("db")
    dlg.names._add_from_query()
    dlg._copy_rule()
    assert dlg.rule_list.count() == 3 and dlg.rules[2]["resourceNames"] == ["db"]
    dlg._move(-1)
    assert dlg.rule_list.currentRow() == 1
    dlg._remove_rule()
    dlg._remove_rule()
    assert dlg.rule_list.count() == 1 and dlg.rules[0]["resources"] == ["pods"]
    dlg._remove_rule()
    assert dlg.rule_list.count() == 0 and not dlg.scroll.isEnabled()


def test_save_keeps_the_rest_of_the_manifest(qtbot):
    dlg = dialog(qtbot)
    dlg.verb_boxes["delete"].setChecked(True)
    dlg._save()
    body = dlg.result_body
    assert dlg.result() == QDialog.Accepted
    assert body["metadata"] == RAW["metadata"] and body["kind"] == "Role"
    assert body["rules"] == [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "delete"]}]
    assert RAW["rules"][0]["verbs"] == ["get", "list"]  # the row's own manifest is not touched


def test_save_is_refused_with_the_problems_listed(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = dialog(qtbot)
    for box in dlg.verb_boxes.values():
        box.setChecked(False)
    dlg._save()
    assert dlg.result_body is None and "Rule 1: choose at least one verb" in shown[0]


def test_granting_everything_asks_first_and_defaults_to_no(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    raw = {**RAW, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}
    dlg = dialog(qtbot, raw)
    dlg._save()
    assert asked and asked[0][-1] == QMessageBox.No and dlg.result_body is None
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dlg._save()
    assert dlg.result_body["rules"] == raw["rules"]


def test_clusterrole_can_hold_url_rules_and_switch_between_kinds_of_rule(qtbot):
    raw = {"metadata": {"name": "c"}, "rules": [{"nonResourceURLs": ["/healthz"], "verbs": ["get"]}]}
    dlg = dialog(qtbot, raw, "ClusterRole")
    assert dlg.type_box.currentIndex() == 1 and not dlg.urls.isHidden() and dlg.groups.isHidden()
    dlg.urls.query.setText("/metrics")
    dlg.urls._add_from_query()
    dlg._save()
    assert dlg.result_body["rules"] == [{"nonResourceURLs": ["/healthz", "/metrics"], "verbs": ["get"]}]
    dlg.type_box.setCurrentIndex(0)  # switching to resources drops the URLs from the rule
    assert "nonResourceURLs" not in dlg.rules[0] and not dlg.groups.isHidden()


def test_edit_as_yaml_rejects_and_flags_it(qtbot):
    dlg = dialog(qtbot)
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.result_body is None and dlg.result() == QDialog.Rejected


def test_dialog_status_follows_the_loader(qtbot):
    index = index_with(CORE)
    loader = ApiIndexLoader(Service(["a", "b"]), sync_run, index)
    dlg = RoleEditDialog("Role", RAW, loader)
    qtbot.addWidget(dlg)
    assert dlg.reload_button.isEnabled()
    with qtbot.waitSignal(loader.finished, timeout=2000):
        loader.start()
    assert "6 resources" in dlg.status.text() and "3 API groups" in dlg.status.text()


# ---- workspace routing ---------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_workspace_edits_roles_with_the_form_and_saves_the_result(qtbot, monkeypatch):
    saved = []
    ws = make_ws(qtbot, replace=lambda kind, body: saved.append((kind, body)), discovery_groups=lambda: [],
                 discovery_resources=lambda g, v: [])
    ws.refresh = lambda: None
    seen = {}

    class FakeForm:
        Accepted = 1

        def __init__(self, kind, raw, loader, parent=None):
            seen.update(kind=kind, loader=loader)
            self.result_body, self.wants_yaml = {"metadata": {"name": "r"}, "rules": []}, False

        def exec(self):
            return 1

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.RoleEditDialog", FakeForm)
    ws.edit_resource("Role", Row("r", "dev", RAW))
    qtbot.waitUntil(lambda: bool(saved), timeout=3000)
    assert saved == [("Role", {"metadata": {"name": "r"}, "rules": []})] and seen["kind"] == "Role"
    assert ws.api_loader() is seen["loader"]  # one index per workspace


def test_aggregated_roles_and_other_kinds_use_the_yaml_editor(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    opened = []

    class YamlDialog:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note="", **_kw):
            opened.append(title)

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.EditDialog", YamlDialog)
    monkeypatch.setattr("klotse.ui.workspace.RoleEditDialog", lambda *a, **k: pytest.fail("form must not open"))
    ws.edit_resource("ClusterRole", Row("agg", "", {"aggregationRule": {"clusterRoleSelectors": []}, "rules": []}))
    ws.edit_resource("PersistentVolumeClaim", Row("pvc", "ns", {"metadata": {"name": "pvc"}}))  # no form editor for this kind
    assert opened == ["Edit ClusterRole agg", "Edit PersistentVolumeClaim pvc"]


def test_edit_as_yaml_from_the_form_opens_the_yaml_editor(qtbot, monkeypatch):
    ws = make_ws(qtbot, discovery_groups=lambda: [], discovery_resources=lambda g, v: [])
    opened = []

    class Form:
        Accepted = 1

        def __init__(self, *a, **k):
            self.wants_yaml, self.result_body = True, None

        def exec(self):
            return 0

        def deleteLater(self):
            pass

    class YamlDialog:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note="", **_kw):
            opened.append(title)

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.RoleEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", YamlDialog)
    ws.edit_resource("Role", Row("r", "dev", RAW))
    assert opened == ["Edit Role r"]


# ---- roles that need care (aggregation, defaults), from the Kubernetes RBAC docs ----
from klotse.ui.role_editor import aggregated_note, confirm_risky, role_warnings, rule_notes

EDIT_FEED = {"metadata": {"name": "my-crd-editor", "labels": {"rbac.authorization.k8s.io/aggregate-to-edit": "true"}},
             "rules": [{"apiGroups": ["example.com"], "resources": ["widgets"], "verbs": ["get"]}]}
SYSTEM = {"metadata": {"name": "system:node", "labels": {"kubernetes.io/bootstrapping": "rbac-defaults"}}, "rules": []}


def test_role_warnings_flag_aggregated_feeders_and_default_roles():
    assert role_warnings(RAW) == []
    (feeder,) = role_warnings(EDIT_FEED)
    assert "aggregated into the ClusterRole edit" in feeder and "every namespace" in feeder
    both = {"metadata": {"name": "system:aggregate-to-view",
                         "labels": {"rbac.authorization.k8s.io/aggregate-to-view": "true",
                                    "kubernetes.io/bootstrapping": "rbac-defaults"}}}
    assert len(role_warnings(both)) == 2 and "view" in role_warnings(both)[0]
    assert "start-up" in role_warnings(SYSTEM)[0]
    off = {"metadata": {**SYSTEM["metadata"], "annotations": {"rbac.authorization.kubernetes.io/autoupdate": "false"}}}
    assert "switched off" in role_warnings(off)[0]
    assert role_warnings({"metadata": {"name": "x", "labels": {"rbac.authorization.k8s.io/aggregate-to-edit": "false"}}}) == []
    assert len(role_warnings({"metadata": {"name": "system:thing"}})) == 1  # the reserved prefix alone is enough


def test_aggregated_note_only_for_roles_with_an_aggregation_rule():
    assert aggregated_note(RAW) == ""
    assert "overwrites its rules" in aggregated_note({"aggregationRule": {"clusterRoleSelectors": []}})


def test_rule_notes_follow_the_docs_on_resource_names():
    base = {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": ["x"]}
    assert rule_notes({**base, "verbs": ["get", "update"]}) == []
    assert "no effect" in rule_notes({**base, "verbs": ["create"]})[0]
    assert "no effect" in rule_notes({**base, "verbs": ["deletecollection"]})[0]
    assert "field selector" in rule_notes({**base, "verbs": ["list"]})[0]
    assert rule_notes({"apiGroups": [""], "resources": ["pods"], "verbs": ["create"]}) == []  # no names, nothing to say


def test_confirm_risky_defaults_to_no_and_skips_when_nothing_to_say(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    assert confirm_risky(None, "Role", "r", []) is True and not asked
    assert confirm_risky(None, "Role", "r", ["careful"]) is False
    assert asked[0][-1] == QMessageBox.No and "careful" in asked[0][2]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    assert confirm_risky(None, "Role", "r", ["careful"]) is True


def test_dialog_shows_a_banner_and_asks_before_saving_a_flagged_role(qtbot, monkeypatch):
    plain = dialog(qtbot)
    assert plain.banner.isHidden()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    dlg = dialog(qtbot, EDIT_FEED, "ClusterRole")
    dlg.show()
    assert not dlg.banner.isHidden() and "edit" in dlg.banner.text()
    dlg._save()
    assert dlg.result_body is None and "aggregated into the ClusterRole edit" in asked[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dlg._save()
    assert dlg.result_body["rules"] == EDIT_FEED["rules"] and dlg.result_body["metadata"] == EDIT_FEED["metadata"]


def test_dialog_hint_mentions_ineffective_resource_names(qtbot):
    raw = {**RAW, "rules": [{"apiGroups": [""], "resources": ["configmaps"], "resourceNames": ["x"], "verbs": ["create"]}]}
    dlg = dialog(qtbot, raw)
    assert "no effect" in dlg.hint.text()


def test_yaml_editor_gets_the_aggregation_note_and_a_confirmation(qtbot, monkeypatch):
    ws = make_ws(qtbot, replace=lambda *a: None)
    ws.refresh = lambda: None
    notes, asked = [], []

    class YamlDialog:
        Accepted = 1

        def __init__(self, title, raw, parent=None, note="", **_kw):
            notes.append(note)
            self.result_body = raw

        def exec(self):
            return 1

    monkeypatch.setattr("klotse.ui.workspace.EditDialog", YamlDialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    agg = {"metadata": {"name": "edit", "labels": {"kubernetes.io/bootstrapping": "rbac-defaults"}},
           "aggregationRule": {"clusterRoleSelectors": []}, "rules": []}
    ws.edit_resource("ClusterRole", Row("edit", "", agg))
    assert "aggregated" in notes[0] and "default (system) role" in notes[0]
    assert asked and "default (system) role" in asked[0]  # declined: nothing was saved, and no worker was started
    assert not ws._workers


def test_deleting_a_flagged_role_repeats_the_warning(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    ws = make_ws(qtbot, delete_resource=lambda *a: pytest.fail("must not delete"))
    ws.delete_resource("ClusterRole", Row("my-crd-editor", "", {**EDIT_FEED, "metadata": {**EDIT_FEED["metadata"], "uid": "u"}}))
    assert "aggregated into the ClusterRole edit" in asked[0] and "Bindings that refer to it" in asked[0]
    ws.bulk_action("ClusterRole", "delete", [Row("a", "", {"metadata": {"name": "a", "uid": "1"}}),
                                             Row("system:x", "", {"metadata": {"name": "system:x", "uid": "2"}})])
    assert "1 of them are default" in asked[1] and "system:x" in asked[1]


def test_resource_names_have_a_help_button_with_a_tooltip(qtbot, monkeypatch):
    dlg = dialog(qtbot)
    dlg.show()
    button = dlg.names.help_button
    tip = button.toolTip()
    assert button.isVisible() and button.text() == "?" and tip == role_editor.RESOURCE_NAMES_HELP
    for phrase in ("exact", "No wildcards", "create", "deletecollection", "field selector", "impersonate", "bind",
                   "Empty: the rule applies to every object"):
        assert phrase in tip
    assert tip.startswith("<")  # rich text, so Qt wraps it instead of drawing one very long line
    shown = []
    monkeypatch.setattr(role_editor.QToolTip, "showText", lambda pos, text, widget=None: shown.append((text, widget)))
    button.click()  # a click shows it at once, without waiting for a hover
    assert shown == [(tip, button)]
    assert dlg.groups.help_button.isHidden() and dlg.resources.help_button.isHidden()  # only where help was written
