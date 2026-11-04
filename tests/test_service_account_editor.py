"""ServiceAccount form editor, the secret-name directory/listing and the routing from the Edit action."""
import json
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row
from klotse.ui import service_account_editor as sae
from klotse.ui.account_directory import AccountDirectory, SecretDirectory
from klotse.ui.binding_editor import care_warnings
from klotse.ui.service_account_editor import (ANNOTATION_SUGGESTIONS, LAST_APPLIED, KeyValueEditor,
                                              ServiceAccountEditDialog, build_body, service_account_warnings,
                                              validate_pairs)
from klotse.ui.workspace import ClusterWorkspace

RAW = {"apiVersion": "v1", "kind": "ServiceAccount",
       "metadata": {"name": "ci", "namespace": "build", "resourceVersion": "5", "uid": "u-1",
                    "labels": {"team": "a"}, "annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::1:role/x",
                                                             LAST_APPLIED: '{"huge": true}'}},
       "automountServiceAccountToken": False,
       "secrets": [{"name": "ci-token", "kind": "Secret", "uid": "keep-me"}],
       "imagePullSecrets": [{"name": "regcred"}]}


def sync_run(fn, on_result, *args, on_error=None):
    try:
        result = fn(*args)
    except Exception as e:
        (on_error or (lambda m: None))(str(e))
        return
    on_result(result)


class Service:
    def __init__(self, names, fail=None):
        self.names, self.fail, self.asked = names, fail, []

    def list_secret_names(self, namespace):
        self.asked.append(namespace)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.names


def directory(*names, namespace="build", fail=None):
    d = SecretDirectory(Service(list(names), fail), sync_run, namespace)
    d.start()
    return d


def make(qtbot, raw=RAW, names=("ci-token", "regcred", "other-secret")):
    dlg = ServiceAccountEditDialog("ServiceAccount", raw, directory(*names))
    qtbot.addWidget(dlg)
    return dlg


# ---- listing secret names -------------------------------------------------------
def test_service_lists_secret_names_asking_only_for_metadata():
    calls = []
    pages = [{"items": [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}], "metadata": {"continue": "tok"}},
             {"items": [{"metadata": {"name": "c"}}], "metadata": {}}]

    def list_secrets(namespace, **kw):
        calls.append((namespace, kw))
        return SimpleNamespace(data=json.dumps(pages[len(calls) - 1]).encode())

    svc = KubeService()
    svc._core = SimpleNamespace(list_namespaced_secret=list_secrets)
    assert svc.list_secret_names("build") == ["a", "b", "c"]
    first, second = calls
    assert first[0] == "build" and first[1]["_continue"] is None and second[1]["_continue"] == "tok"
    assert "PartialObjectMetadataList" in first[1]["_headers"]["Accept"]  # metadata only: no secret data is sent
    assert first[1]["_preload_content"] is False and first[1]["limit"] == 500


def test_secret_directory_searches_names_with_a_cap_and_reports_status(qtbot):
    d = directory("zeta", "alpha", "alpha", "beta-token")
    assert d.entries == ["alpha", "beta-token", "zeta"] and d.status_text() == "3 secrets in build"
    assert d.search("TOKEN") == (["beta-token"], False) and d.search("none") == ([], False)
    big = directory(*[f"s{i}" for i in range(500)])
    hits, more = big.search("", limit=200)
    assert len(hits) == 200 and more
    assert d.service.asked == ["build"]


def test_secret_directory_failure_still_allows_typing(qtbot):
    d = directory(fail="forbidden")
    assert d.state == "failed" and "forbidden" in d.status_text() and "type a name" in d.status_text()


def test_the_account_directory_still_asks_for_service_accounts(qtbot):
    class Accounts:
        def list_service_account_names(self):
            return [("a", "b")]

    d = AccountDirectory(Accounts(), sync_run)
    d.start()
    assert d.entries == [("a", "b")]


# ---- pure helpers ---------------------------------------------------------------
def test_validate_pairs():
    assert validate_pairs([("a", "1"), ("b", "")], "label") == []
    problems = validate_pairs([("", "x"), ("has space", ""), ("k=v", ""), ("dup", "1"), ("dup", "2")], "annotation")
    assert len(problems) == 4 and problems[0] == "Annotation 1: enter a key"
    assert "spaces or '='" in problems[1] and "listed twice" in problems[3]


def test_warnings_for_default_and_kube_system_accounts():
    assert service_account_warnings(RAW) == []
    default = {"metadata": {"name": "default", "namespace": "dev"}}
    assert "every pod that does not name a service account" in service_account_warnings(default)[0]
    assert "recreates it" in service_account_warnings(default)[0]
    assert "kube-system" in service_account_warnings({"metadata": {"name": "x", "namespace": "kube-system"}})[0]
    assert care_warnings("ServiceAccount", default) == service_account_warnings(default)


def test_build_body_changes_only_the_edited_fields():
    body = build_body(RAW, True, ["ci-token", "new-secret"], [], {"team": "b"}, {"x/y": "z", LAST_APPLIED: "kept"})
    assert body["automountServiceAccountToken"] is True and "imagePullSecrets" not in body
    assert body["secrets"] == [{"name": "ci-token", "kind": "Secret", "uid": "keep-me"}, {"name": "new-secret"}]
    assert body["metadata"]["labels"] == {"team": "b"} and body["metadata"]["annotations"] == {"x/y": "z", LAST_APPLIED: "kept"}
    assert body["metadata"]["resourceVersion"] == "5" and body["kind"] == "ServiceAccount"
    assert RAW["secrets"][0]["uid"] == "keep-me" and RAW["automountServiceAccountToken"] is False  # input untouched
    bare = build_body({"metadata": {"name": "a"}, "automountServiceAccountToken": True}, None, [], [], {}, {})
    assert bare == {"metadata": {"name": "a"}}  # unset and empty fields are removed, not written empty


# ---- key/value editor -----------------------------------------------------------
def test_key_value_editor_round_trips_and_hides_the_long_annotation(qtbot):
    kv = KeyValueEditor("Annotations", ANNOTATION_SUGGESTIONS, hidden_keys=(LAST_APPLIED,))
    qtbot.addWidget(kv)
    kv.set_items(dict(RAW["metadata"]["annotations"]))
    assert kv.table.rowCount() == 1 and kv.table.item(0, 0).text() == "eks.amazonaws.com/role-arn"
    assert kv.items() == RAW["metadata"]["annotations"]  # the hidden one comes back unchanged
    assert [kv.key_box.itemText(i) for i in range(kv.key_box.count())] == list(ANNOTATION_SUGGESTIONS)


def test_key_value_editor_adds_updates_and_removes(qtbot):
    kv = KeyValueEditor("Labels")
    qtbot.addWidget(kv)
    seen = []
    kv.changed.connect(lambda: seen.append(1))
    kv.set_items({"a": "1"})
    kv.key_box.setCurrentText("b")
    kv.value_edit.setText("2")
    kv._add()
    kv.key_box.setCurrentText("a")
    kv.value_edit.setText("changed")
    kv._add()  # an existing key is updated, not repeated
    assert kv.items() == {"a": "changed", "b": "2"} and kv.table.rowCount() == 2
    assert kv.key_box.currentText() == "" and kv.value_edit.text() == "" and seen
    kv.key_box.setCurrentText("  ")
    kv._add()  # a blank key adds nothing
    assert kv.table.rowCount() == 2
    kv.table.selectRow(0)
    kv._remove()
    assert kv.items() == {"b": "2"}


# ---- the dialog -----------------------------------------------------------------
def test_dialog_shows_the_account_and_its_fields(qtbot):
    dlg = make(qtbot)
    assert dlg.automount.currentIndex() == 2  # automount is explicitly off
    assert dlg.secrets.values() == ["ci-token"] and dlg.pull_secrets.values() == ["regcred"]
    assert dlg.annotations.table.rowCount() == 1 and dlg.labels.table.rowCount() == 1
    assert dlg.banner.isHidden() and "3 secrets in build" in dlg.directory_status.text()
    unset = make(qtbot, {"metadata": {"name": "x", "namespace": "n"}})
    assert unset.automount.currentIndex() == 0


def test_secrets_are_picked_from_the_namespace_or_typed(qtbot):
    dlg = make(qtbot)
    dlg.pull_secrets.query.setText("other")
    dlg.pull_secrets.refresh()
    dlg.pull_secrets.results.item(0).setSelected(True)
    dlg.pull_secrets._add_selected()
    assert dlg.pull_secrets.values() == ["regcred", "other-secret"]
    dlg.secrets.query.setText("brand-new")  # not listed: the secret may not exist yet
    dlg._add_typed(dlg.secrets)
    assert dlg.secrets.values() == ["ci-token", "brand-new"] and dlg.secrets.query.text() == ""
    dlg._add_typed(dlg.secrets)  # an empty box adds nothing


def test_save_writes_the_edited_fields_and_keeps_the_rest(qtbot):
    dlg = make(qtbot)
    dlg.automount.setCurrentIndex(1)
    dlg.pull_secrets.query.setText("second")
    dlg._add_typed(dlg.pull_secrets)
    dlg.annotations.key_box.setCurrentText("iam.gke.io/gcp-service-account")
    dlg.annotations.value_edit.setText("sa@project.iam.gserviceaccount.com")
    dlg.annotations._add()
    dlg._save()
    body = dlg.result_body
    assert dlg.result() == QDialog.Accepted and body["automountServiceAccountToken"] is True
    assert body["imagePullSecrets"] == [{"name": "regcred"}, {"name": "second"}]
    assert body["secrets"] == RAW["secrets"]  # the untouched secret entry keeps all its fields
    notes = body["metadata"]["annotations"]
    assert notes["iam.gke.io/gcp-service-account"] == "sa@project.iam.gserviceaccount.com"
    assert notes[LAST_APPLIED] == RAW["metadata"]["annotations"][LAST_APPLIED]
    assert body["metadata"]["uid"] == "u-1" and body["metadata"]["labels"] == {"team": "a"}


def test_saving_an_untouched_account_changes_nothing(qtbot):
    dlg = make(qtbot)
    dlg._save()
    assert dlg.result_body == RAW


def test_save_is_refused_with_invalid_keys(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    dlg = make(qtbot)
    dlg.labels.table.item(0, 0).setText("bad key")
    assert "spaces" in dlg.hint.text()
    dlg._save()
    assert dlg.result_body is None and "Label 1" in shown[0]


def test_the_default_account_shows_a_banner_and_asks_first(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    raw = {"metadata": {"name": "default", "namespace": "dev"}}
    dlg = make(qtbot, raw)
    dlg.show()
    assert not dlg.banner.isHidden() and "default service account" in dlg.banner.text()
    dlg._save()
    assert dlg.result_body is None and "every pod" in asked[0]


def test_text_from_the_cluster_is_escaped_in_the_header(qtbot):
    dlg = make(qtbot, {"metadata": {"name": "<b>x</b>", "namespace": "<i>n</i>"}})
    assert "&lt;b&gt;x&lt;/b&gt;" in dlg.head.text() and "&lt;i&gt;n&lt;/i&gt;" in dlg.head.text()
    assert "<i>n</i>" not in dlg.head.text() and "<b>x</b>" not in dlg.head.text()


def test_help_texts_explain_secrets_and_pull_secrets(qtbot):
    dlg = make(qtbot)
    assert dlg.secrets.help_button.toolTip() == sae.SECRETS_HELP and "TokenRequest" in sae.SECRETS_HELP
    assert dlg.pull_secrets.help_button.toolTip() == sae.PULL_SECRETS_HELP and "private registry" in sae.PULL_SECRETS_HELP
    assert "wins over" in dlg.automount.toolTip()


def test_edit_as_yaml_rejects_and_flags_it(qtbot):
    dlg = make(qtbot)
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.result_body is None and dlg.result() == QDialog.Rejected


# ---- workspace routing ----------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_workspace_edits_service_accounts_with_the_form_and_loads_names_per_namespace(qtbot, monkeypatch):
    saved, seen = [], []
    ws = make_ws(qtbot, replace=lambda kind, body: saved.append((kind, body)), list_secret_names=lambda ns: [ns + "-s"])
    ws.refresh = lambda: None

    class FakeForm:
        def __init__(self, kind, raw, directory, parent=None):
            seen.append((kind, directory))
            self.result_body, self.wants_yaml = {"metadata": {"name": "ci"}}, False

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.ServiceAccountEditDialog", FakeForm)
    ws.edit_resource("ServiceAccount", Row("ci", "build", RAW))
    qtbot.waitUntil(lambda: bool(saved), timeout=3000)
    assert saved == [("ServiceAccount", {"metadata": {"name": "ci"}})] and seen[0][0] == "ServiceAccount"
    assert seen[0][1].namespace == "build"
    qtbot.waitUntil(lambda: seen[0][1].state == "done", timeout=3000)
    assert seen[0][1].entries == ["build-s"]
    assert ws.secret_directory("build") is seen[0][1] and ws.secret_directory("other") is not seen[0][1]


def test_edit_as_yaml_from_the_account_form_opens_the_yaml_editor_with_the_note(qtbot, monkeypatch):
    ws = make_ws(qtbot, list_secret_names=lambda ns: [])
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

    monkeypatch.setattr("klotse.ui.workspace.ServiceAccountEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", YamlDialog)
    ws.edit_resource("ServiceAccount", Row("default", "dev", {"metadata": {"name": "default", "namespace": "dev"}}))
    assert opened[0][0] == "Edit ServiceAccount default" and "default service account" in opened[0][1]


def test_deleting_the_default_account_repeats_the_warning(qtbot, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    ws = make_ws(qtbot, delete_resource=lambda *a: pytest.fail("must not delete"))
    raw = {"metadata": {"name": "default", "namespace": "dev", "uid": "u"}}
    ws.delete_resource("ServiceAccount", Row("default", "dev", raw))
    assert "Pods that use it can no longer be created" in asked[0] and "recreates it" in asked[0]
