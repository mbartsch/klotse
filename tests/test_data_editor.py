"""ConfigMap and Secret form: per-key reveal (only that key is decoded), patches, creating, and the workspace routing."""
import base64
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.resources import Row
from klotse.ui import data_editor as de
from klotse.ui.data_editor import DataEditDialog
from klotse.ui.workspace import ClusterWorkspace


def b64(text):
    return base64.b64encode(text.encode()).decode()


SECRET_RAW = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": "db", "namespace": "shop", "labels": {"app": "db"},
                           "annotations": {de.LAST_APPLIED: "<hidden>", "note": "x"}},
              "data": {"user": "<hidden>", "password": "<hidden>", "blob": "<hidden>"}}
SECRET_DATA = {"user": b64("admin"), "password": b64("s3cr3t\n"), "blob": base64.b64encode(b"\xff\xfe\x00").decode()}
CM_RAW = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "conf", "namespace": "shop", "labels": {"a": "b"}},
          "data": {"app.ini": "[main]\nport = 80\n", "motd": "hello"}, "binaryData": {"logo": b64("PNGDATA")}}


def secret_form(qtbot, data=None, raw=None):
    dlg = DataEditDialog("Secret", dict(raw or SECRET_RAW), secret_data=dict(data or SECRET_DATA))
    qtbot.addWidget(dlg)
    return dlg


def cm_form(qtbot, raw=None):
    dlg = DataEditDialog("ConfigMap", dict(raw or CM_RAW))
    qtbot.addWidget(dlg)
    return dlg


def select(dlg, key):
    names = dlg.data.keys_in_use()
    dlg.data.keys.setCurrentRow(names.index(key))


# ---- helpers ------------------------------------------------------------------------------
def test_keys_follow_the_kubernetes_rule():
    assert all(de.valid_key(k) for k in ("a", "app.ini", ".dockerconfigjson", "tls_crt", "A-1"))
    assert not any(de.valid_key(k) for k in ("", ".", "..", "a b", "a/b", "x" * 254))


def test_base64_helpers_never_raise_and_report_binary():
    assert de.decode_text(b64("héllo")) == "héllo" and de.decode_text("!!!") is None
    assert de.decode_text(base64.b64encode(b"\xff\xfe").decode()) is None
    assert de.encode_text("héllo") == b64("héllo") and de.decoded_size(b64("12345678")) == 8


def test_the_patch_holds_only_what_changed():
    old = {"a": "QQ==", "b": "Qg==", "c": "Qw=="}
    new = {"a": "QQ==", "b": "WA==", "d": "RA=="}
    meta_old = {"labels": {"x": "1", "y": "2"}, "annotations": {de.LAST_APPLIED: "<hidden>", "n": "1"}}
    meta_new = {"labels": {"x": "1", "z": "3"}, "annotations": {de.LAST_APPLIED: "<hidden>", "n": "1"}}
    assert de.secret_patch(old, new, meta_old, meta_new) == {
        "data": {"b": "WA==", "d": "RA==", "c": None}, "metadata": {"labels": {"z": "3", "y": None}}}
    assert de.secret_patch(old, old, meta_old, meta_old) == {}


def test_validation_messages():
    assert de.validate_data(["a", "b"], 10) == []
    assert any("twice" in p for p in de.validate_data(["a", "a"], 10))
    assert any("letters" in p for p in de.validate_data(["a b"], 10))
    assert any("1 MiB" in p for p in de.validate_data(["a"], de.SIZE_LIMIT + 1))
    assert de.validate_data(["a"], 1, {"name": "ok-name", "namespace": "ns"}) == []
    assert len(de.validate_data(["a"], 1, {"name": "Bad", "namespace": ""})) == 2


# ---- secrets: nothing is decoded until a key is shown ------------------------------------------------
def test_a_secret_shows_hidden_and_decodes_nothing_up_front(qtbot, monkeypatch):
    calls = []
    real = de.decode_text
    monkeypatch.setattr(de, "decode_text", lambda s: calls.append(s) or real(s))
    dlg = secret_form(qtbot)
    select(dlg, "user")
    assert dlg.data.pages.currentIndex() == 2 and dlg.data.hidden_label.text() == "<hidden>"
    assert calls == []  # opening and selecting decoded nothing
    assert dlg.data.editor.toPlainText() == ""


def test_show_decodes_only_the_chosen_key(qtbot, monkeypatch):
    calls = []
    real = de.decode_text
    monkeypatch.setattr(de, "decode_text", lambda s: calls.append(s) or real(s))
    dlg = secret_form(qtbot)
    select(dlg, "user")
    dlg.data.show_button.click()
    assert calls == [SECRET_DATA["user"]] and dlg.data.pages.currentIndex() == 1
    assert dlg.data.editor.toPlainText() == "admin"
    select(dlg, "password")
    assert dlg.data.pages.currentIndex() == 2  # the next key is still hidden
    assert calls == [SECRET_DATA["user"]]
    select(dlg, "user")
    dlg.data.hide_button.click()  # hiding again masks it
    assert dlg.data.pages.currentIndex() == 2  # the value is off the screen again


def test_an_untouched_secret_sends_nothing(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    dlg = secret_form(qtbot)
    dlg._save()
    assert dlg.result_body is None and shown == ["Nothing was changed."]


def test_viewing_a_key_without_editing_it_does_not_change_it(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    dlg = secret_form(qtbot)
    select(dlg, "password")
    dlg.data.show_button.click()  # trailing newline and all
    dlg._save()
    assert dlg.result_body is None  # no patch: the original base64 is kept


def test_editing_one_key_patches_only_that_key(qtbot):
    dlg = secret_form(qtbot)
    select(dlg, "user")
    dlg.data.show_button.click()
    dlg.data.editor.setPlainText("root")
    dlg._save()
    assert dlg.is_patch and dlg.result_body == {"data": {"user": b64("root")}}


def test_add_remove_and_rename_keys_make_a_patch(qtbot):
    dlg = secret_form(qtbot)
    dlg.data.new_key.setText("token")
    dlg.data._add()
    dlg.data.editor.setPlainText("t0k3n")
    select(dlg, "blob")
    dlg.data._remove()
    select(dlg, "user")
    dlg.data.keys.item(0).setText("username")  # rename: the same stored value under a new key
    dlg._save()
    assert dlg.result_body == {"data": {"token": b64("t0k3n"), "blob": None, "user": None, "username": SECRET_DATA["user"]}}


def test_a_binary_value_is_never_shown_or_changed(qtbot):
    dlg = secret_form(qtbot)
    select(dlg, "blob")
    dlg.data.show_button.click()
    assert dlg.data.pages.currentIndex() == 3 and "Binary value, 3 bytes" in dlg.data.binary_label.text()
    assert not dlg.data.keys.item(dlg.data.keys_in_use().index("blob")).flags() & dlg.data.keys.item(0).flags().ItemIsEditable
    assert dlg.data.stored_data()[0]["blob"] == SECRET_DATA["blob"]


def test_labels_and_annotations_patch_without_touching_the_masked_annotation(qtbot):
    dlg = secret_form(qtbot)
    dlg.labels.key_box.setEditText("tier")
    dlg.labels.value_edit.setText("db")
    dlg.labels._add()
    dlg.annotations.table.selectAll()
    dlg.annotations._remove()  # the hidden last-applied entry is not in the table, so it is not "removed"
    dlg._save()
    assert dlg.result_body == {"metadata": {"labels": {"tier": "db"}, "annotations": {"note": None}}}


def test_a_secret_has_no_yaml_button_and_no_pending_body(qtbot):
    dlg = secret_form(qtbot)
    assert dlg.yaml_button.isHidden() and dlg.pending_body() is None


def test_the_type_is_shown_but_cannot_be_changed(qtbot):
    dlg = secret_form(qtbot)
    assert dlg.type_box is None and any(w.text() == "Opaque" for w in dlg.findChildren(type(dlg.head)))


def test_an_immutable_secret_can_only_change_labels(qtbot):
    raw = {**SECRET_RAW, "immutable": True}
    dlg = secret_form(qtbot, raw=raw)
    assert not dlg.banner.isHidden() or "immutable" in dlg.banner.text()
    assert not dlg.data.add_button.isEnabled() and not dlg.data.remove_button.isEnabled()


# ---- config maps ------------------------------------------------------------------------------------------------
def test_a_config_map_shows_its_text_and_keeps_binary(qtbot):
    dlg = cm_form(qtbot)
    select(dlg, "app.ini")
    assert dlg.data.pages.currentIndex() == 1 and dlg.data.editor.toPlainText() == "[main]\nport = 80\n"
    select(dlg, "logo")
    assert dlg.data.pages.currentIndex() == 3
    dlg._save()
    assert dlg.result_body == CM_RAW  # unchanged in, unchanged out


def test_a_config_map_edit_replaces_text_and_keeps_everything_else(qtbot):
    dlg = cm_form(qtbot)
    select(dlg, "motd")
    dlg.data.editor.setPlainText("bye\nnow")
    dlg.data.new_key.setText("extra")
    dlg.data._add()
    dlg.data.editor.setPlainText("1")
    select(dlg, "app.ini")
    dlg.data._remove()
    dlg._save()
    body = dlg.result_body
    assert body["data"] == {"motd": "bye\nnow", "extra": "1"} and body["binaryData"] == CM_RAW["binaryData"]
    assert body["metadata"] == CM_RAW["metadata"] and not dlg.is_patch


def test_an_immutable_config_map_keeps_its_data(qtbot):
    raw = {**CM_RAW, "immutable": True}
    dlg = cm_form(qtbot, raw)
    assert not dlg.data.editor.isEnabled() or dlg.data.editor.isReadOnly()
    dlg.labels.key_box.setEditText("x")
    dlg.labels.value_edit.setText("y")
    dlg.labels._add()
    dlg._save()
    assert dlg.result_body["data"] == CM_RAW["data"] and dlg.result_body["metadata"]["labels"] == {"a": "b", "x": "y"}


def test_a_config_map_hands_its_values_to_the_yaml_editor(qtbot):
    dlg = cm_form(qtbot)
    select(dlg, "motd")
    dlg.data.editor.setPlainText("changed")
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.pending["data"]["motd"] == "changed"


def test_bad_keys_block_saving(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = cm_form(qtbot)
    dlg.data.new_key.setText("bad key")
    dlg.data._add()
    dlg._save()
    assert dlg.result_body is None and "letters" in shown[0]


# ---- creating --------------------------------------------------------------------------------------------------
def test_a_new_secret_is_created_with_the_typed_values_and_type(qtbot):
    dlg = DataEditDialog("Secret", de.new_manifest("Secret", "shop"), create=True, namespaces=["default", "shop"])
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "New Secret" and dlg.namespace_edit.currentText() == "shop"
    assert dlg.type_box.currentText() == "Opaque" and dlg.type_note.text()
    dlg.name_edit.setText("creds")
    dlg.type_box.setCurrentText("kubernetes.io/basic-auth")
    dlg.data.new_key.setText("username")
    dlg.data._add()
    dlg.data.editor.setPlainText("bob")
    dlg._save()
    assert not dlg.is_patch
    assert dlg.result_body == {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/basic-auth",
                               "metadata": {"name": "creds", "namespace": "shop"}, "data": {"username": b64("bob")}}


def test_a_new_config_map_needs_a_name(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = DataEditDialog("ConfigMap", de.new_manifest("ConfigMap", "shop"), create=True)
    qtbot.addWidget(dlg)
    dlg._save()
    assert dlg.result_body is None and "Enter a name" in shown[0]
    dlg.name_edit.setText("settings")
    dlg.data.new_key.setText("a")
    dlg.data._add()
    dlg.data.editor.setPlainText("1")
    dlg._save()
    assert dlg.result_body["metadata"]["name"] == "settings" and dlg.result_body["data"] == {"a": "1"}


# ---- workspace and service ---------------------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    return ws


def test_editing_a_secret_sends_a_patch_with_its_namespace(qtbot, monkeypatch):
    patched, seen = [], []
    ws = make_ws(qtbot, patch_secret=lambda ns, name, body: patched.append((ns, name, body)))

    class Form:
        def __init__(self, kind, raw, parent=None, secret_data=None, create=False, namespaces=()):
            seen.append((kind, secret_data, create))
            self.wants_yaml, self.result_body = False, {"data": {"user": "cm9vdA=="}}

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.DataEditDialog", Form)
    row = Row("db", "shop", SECRET_RAW)
    row.secret_data = SECRET_DATA
    ws.edit_resource("Secret", row)
    qtbot.waitUntil(lambda: bool(patched), timeout=3000)
    assert patched == [("shop", "db", {"data": {"user": "cm9vdA=="}})] and seen == [("Secret", SECRET_DATA, False)]


def test_a_secret_never_goes_to_the_yaml_editor(qtbot, monkeypatch):
    opened = []
    ws = make_ws(qtbot)

    class Form:
        def __init__(self, *a, **k):
            self.wants_yaml, self.result_body = False, None

        def exec(self):
            return QDialog.Rejected

        def deleteLater(self):
            pass

    class Yaml:
        def __init__(self, *a, **k):
            opened.append(1)

    monkeypatch.setattr("klotse.ui.workspace.DataEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", Yaml)
    ws.edit_resource("Secret", Row("db", "shop", SECRET_RAW))
    assert opened == []  # cancelling the form just closes it


@pytest.mark.parametrize("kind, key", [("ConfigMap", "configmaps"), ("Secret", "secrets")])
def test_config_maps_and_secrets_have_new_and_edit(qtbot, kind, key):
    ws = make_ws(qtbot)
    table = ws.views[key][0]
    assert table.kind == kind and table.new_button is not None and table.editable


def test_creating_a_secret_goes_through_the_service(qtbot, monkeypatch):
    created = []
    ws = make_ws(qtbot, create=lambda kind, body: created.append((kind, body)))

    class Form:
        def __init__(self, kind, raw, parent=None, **kw):
            self.wants_yaml = False
            self.result_body = {"metadata": {"name": "creds", "namespace": "shop"}, "kind": "Secret"}

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.DataEditDialog", Form)
    ws.create_resource("Secret")
    qtbot.waitUntil(lambda: bool(created), timeout=3000)
    assert created[0][0] == "Secret"


def test_the_service_creates_config_maps_and_secrets_and_patches_secrets():
    calls = []
    meta = SimpleNamespace(name="x", namespace="ns", uid="u", creation_timestamp=None, labels=None, annotations=None)
    obj = SimpleNamespace(metadata=meta, data={}, binary_data=None, type="Opaque")
    svc = object.__new__(KubeService)
    svc._core = SimpleNamespace(
        create_namespaced_config_map=lambda ns, body: calls.append(("cm", ns)) or obj,
        create_namespaced_secret=lambda ns, body: calls.append(("secret", ns)) or obj,
        patch_namespaced_secret=lambda name, ns, body: calls.append(("patch", name, ns, body)) or obj)
    for kind in ("ConfigMap", "Secret"):
        try:
            svc.create(kind, {"metadata": {"name": "n", "namespace": "ns"}})
        except AttributeError:
            pass  # the fake object lacks fields the row mapper reads; the API call is what matters
    try:
        svc.patch_secret("ns", "n", {"data": {"a": None}})
    except AttributeError:
        pass
    assert calls == [("cm", "ns"), ("secret", "ns"), ("patch", "n", "ns", {"data": {"a": None}})]
