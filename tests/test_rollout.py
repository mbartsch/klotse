"""A Deployment's rollout history and undo (kubectl rollout history / undo)."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s import rollout
from klotse.k8s.client import KubeService, ReadOnlyError
from klotse.ui.rollout_dialog import RolloutDialog, created_text
from klotse.ui.workspace import ClusterWorkspace

DEPLOYMENT = {"metadata": {"name": "web", "namespace": "shop", "uid": "d1", "annotations": {rollout.REVISION: "3"}},
              "spec": {"selector": {"matchLabels": {"app": "web", "tier": "front"}}}}


def rs(name, revision, image, owner="d1", replicas=0, cause=None, stamp="2026-01-01T00:00:00Z"):
    annotations = {rollout.REVISION: str(revision), **({rollout.CHANGE_CAUSE: cause} if cause else {})}
    return {"metadata": {"name": name, "creationTimestamp": stamp, "annotations": annotations,
                         "ownerReferences": [{"kind": "Deployment", "name": "web", "uid": owner}]},
            "spec": {"replicas": replicas, "template": {"metadata": {"labels": {"app": "web", "pod-template-hash": "abc"}},
                                                        "spec": {"containers": [{"name": "c", "image": image}]}}}}


SETS = [rs("web-1", 1, "nginx:1.24"), rs("web-3", 3, "nginx:1.26", replicas=3, cause="bump to 1.26"), rs("web-2", 2, "nginx:1.25"),
        rs("other-9", 9, "x:1", owner="someone-else"), {"metadata": {"name": "no-revision", "ownerReferences": [{"kind": "Deployment", "uid": "d1"}]}}]


# ---- the pure side -----------------------------------------------------------------------------------------------
def test_the_selector_is_sorted_and_empty_is_empty():
    assert rollout.match_selector({"tier": "front", "app": "web"}) == "app=web,tier=front"
    assert rollout.match_selector(None) == ""


def test_history_is_newest_first_owned_only_and_marks_the_current_revision():
    rows = rollout.history(DEPLOYMENT, SETS)
    assert [r["revision"] for r in rows] == [3, 2, 1]  # another owner's set and one without a revision are left out
    assert [r["current"] for r in rows] == [True, False, False]
    top = rows[0]
    assert (top["name"], top["images"], top["replicas"], top["change_cause"]) == ("web-3", ["nginx:1.26"], 3, "bump to 1.26")


def test_odd_input_gives_an_empty_or_partial_history_never_an_error():
    assert rollout.history({}, []) == [] and rollout.history(None, None) == []
    bad = {"metadata": {"annotations": {rollout.REVISION: "x"}, "ownerReferences": [{"kind": "Deployment", "uid": "d1"}]}}
    assert rollout.history(DEPLOYMENT, [bad, {"spec": None}]) == []


def test_the_rollback_template_drops_the_hash_label_and_leaves_the_set_alone():
    source = rs("web-2", 2, "nginx:1.25")
    before = deepcopy(source)
    template = rollout.rollback_template(source)
    assert template["metadata"]["labels"] == {"app": "web"} and template["spec"]["containers"][0]["image"] == "nginx:1.25"
    assert source == before


@pytest.mark.parametrize("bad", [{}, {"spec": {}}, {"spec": {"template": None}}, None])
def test_a_set_without_a_template_cannot_be_rolled_back_to(bad):
    with pytest.raises(ValueError, match="no pod template"):
        rollout.rollback_template(bad)


def test_ownership_is_by_uid():
    assert rollout.owned_by(rs("a", 1, "x"), DEPLOYMENT) and not rollout.owned_by(rs("a", 1, "x", owner="z"), DEPLOYMENT)
    assert not rollout.owned_by(rs("a", 1, "x"), {"metadata": {}})


# ---- the service -------------------------------------------------------------------------------------------------
class Apps:
    def __init__(self):
        self.patches = []

    def read_namespaced_deployment(self, name, namespace):
        return DEPLOYMENT

    def read_namespaced_replica_set(self, name, namespace):
        return next(s for s in SETS if s["metadata"]["name"] == name)

    def list_namespaced_replica_set(self, namespace, **kw):
        self.selector = kw["label_selector"]
        return SimpleNamespace(items=SETS, metadata=SimpleNamespace(_continue=None))

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patches.append((name, namespace, body))


def service():
    svc = object.__new__(KubeService)
    svc._apps = Apps()
    return svc


def test_the_service_lists_the_history_through_the_deployments_selector():
    svc = service()
    assert [r["revision"] for r in svc.deployment_history("shop", "web")] == [3, 2, 1]
    assert svc._apps.selector == "app=web,tier=front"


def test_the_service_puts_the_old_template_back_with_a_json_patch():
    svc = service()
    svc.rollback_deployment("shop", "web", "web-2")
    [(name, ns, body)] = svc._apps.patches
    assert (name, ns) == ("web", "shop") and body[0]["op"] == "replace" and body[0]["path"] == "/spec/template"
    assert body[0]["value"]["spec"]["containers"][0]["image"] == "nginx:1.25" and "pod-template-hash" not in body[0]["value"]["metadata"]["labels"]


def test_the_service_refuses_a_set_the_deployment_does_not_own_and_a_read_only_cluster():
    svc = service()
    with pytest.raises(ValueError, match="does not belong"):
        svc.rollback_deployment("shop", "web", "other-9")
    assert svc._apps.patches == []
    svc.read_only = True
    with pytest.raises(ReadOnlyError):
        svc.rollback_deployment("shop", "web", "web-2")


# ---- the dialog --------------------------------------------------------------------------------------------------
REVS = rollout.history(DEPLOYMENT, SETS)


def dialog(qtbot, can_change=True, revisions=REVS):
    d = RolloutDialog("ctx", "shop/web", revisions, can_change=can_change)
    qtbot.addWidget(d)
    return d


def test_the_table_lists_revisions_with_the_current_one_marked_in_words(qtbot):
    d = dialog(qtbot)
    assert d.table.rowCount() == 3
    cells = [d.table.item(0, c).text() for c in range(6)]
    assert cells[0] == "3" and cells[1] == "✔ current" and cells[3] == "nginx:1.26" and cells[4] == "3" and cells[5] == "bump to 1.26"
    assert d.table.item(1, 1).text() == ""


def test_rollback_is_offered_only_for_an_earlier_revision(qtbot):
    d = dialog(qtbot)
    asked = []
    d.rollback_requested.connect(asked.append)
    assert not d.rollback_button.isEnabled()  # nothing selected
    d.table.selectRow(0)
    assert not d.rollback_button.isEnabled() and "now" in d.rollback_button.toolTip()  # the current one
    d.table.selectRow(1)
    assert d.rollback_button.isEnabled()
    d.rollback_button.click()
    assert [r["revision"] for r in asked] == [2]


def test_a_read_only_cluster_can_look_but_not_roll_back(qtbot):
    d = dialog(qtbot, can_change=False)
    d.table.selectRow(2)
    assert not d.rollback_button.isEnabled() and "read-only" in d.note.text()


def test_no_revisions_says_why(qtbot):
    d = dialog(qtbot, revisions=[])
    assert d.table.rowCount() == 0 and "No revisions" in d.note.text()


def test_a_creation_time_shows_as_an_age_or_as_it_came():
    assert created_text("2020-01-01T00:00:00Z").endswith("d") and created_text("nonsense") == "nonsense" and created_text(None) == ""


# ---- the workspace -----------------------------------------------------------------------------------------------
def row():
    return SimpleNamespace(name="web", namespace="shop", raw=DEPLOYMENT)


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    return ws


def test_the_row_menu_signal_opens_the_history_dialog(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(RolloutDialog, "exec", lambda self: shown.append(self.table.rowCount()) or QDialog.Accepted)
    ws = make_ws(qtbot, deployment_history=lambda ns, name: REVS)
    ws.deploy_table.history_requested.emit("Deployment", row())
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert shown == [3]


def answer(monkeypatch, button):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: asked.append(a[2]) or button))
    return asked


def test_a_confirmed_rollback_calls_the_service_and_reloads_the_list(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, deployment_history=lambda ns, name: REVS[:2], rollback_deployment=lambda *a: calls.append(a))
    d = ws.rollout_dialog(row(), REVS)
    qtbot.addWidget(d)
    asked = answer(monkeypatch, QMessageBox.Yes)
    d.table.selectRow(1)
    d.rollback_button.click()
    qtbot.waitUntil(lambda: d.table.rowCount() == 2, timeout=5000)
    assert calls == [("shop", "web", "web-2")] and "revision 2" in asked[0] and "nginx:1.25" in asked[0]
    assert "Rolled back to revision 2" in d.status.text()


def test_a_declined_rollback_does_nothing(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, rollback_deployment=lambda *a: calls.append(a))
    d = ws.rollout_dialog(row(), REVS)
    qtbot.addWidget(d)
    answer(monkeypatch, QMessageBox.No)
    d.table.selectRow(1)
    d.rollback_button.click()
    assert calls == [] and d.status.text() == ""


def test_a_failed_rollback_is_shown_in_the_dialog(qtbot, monkeypatch):
    def boom(*a):
        raise RuntimeError("conflict")
    ws = make_ws(qtbot, rollback_deployment=boom)
    d = ws.rollout_dialog(row(), REVS)
    qtbot.addWidget(d)
    answer(monkeypatch, QMessageBox.Yes)
    d.table.selectRow(1)
    d.rollback_button.click()
    qtbot.waitUntil(lambda: "conflict" in d.status.text(), timeout=5000)


def test_a_read_only_workspace_opens_the_history_read_only(qtbot):
    ws = make_ws(qtbot)
    ws.set_read_only(True)
    d = ws.rollout_dialog(row(), REVS)
    qtbot.addWidget(d)
    d.table.selectRow(1)
    assert not d.rollback_button.isEnabled()
