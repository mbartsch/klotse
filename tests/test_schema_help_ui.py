from types import SimpleNamespace

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication

from klotse.ui.edit_dialog import EditDialog
from klotse.ui.workspace import ClusterWorkspace
from test_schema_help import DOC, TEXT


def dialog_with_text(qtbot, text):
    dialog = EditDialog("Edit Deployment web", {"metadata": {"name": "web"}})
    qtbot.addWidget(dialog)
    dialog.editor.setPlainText(text)
    return dialog


def put_cursor(dialog, line, column=None):
    cursor = dialog.editor.textCursor()
    cursor.movePosition(QTextCursor.Start)
    cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, line)
    cursor.movePosition(QTextCursor.EndOfLine if column is None else QTextCursor.Right, QTextCursor.MoveAnchor, column or 1)
    dialog.editor.setTextCursor(cursor)


def line_of(text, needle):
    return next(i for i, line in enumerate(text.split("\n")) if needle in line)


def test_before_the_schema_arrives_the_editor_says_so_and_still_works(qtbot):
    dialog = dialog_with_text(qtbot, TEXT)
    assert "waiting" in dialog.docs.toPlainText() or "Schema help" in dialog.docs.toPlainText()
    dialog.help.set_loading()
    assert "loading" in dialog.docs.toPlainText()
    dialog.help.set_unavailable("the cluster has no OpenAPI v3")
    assert "not available" in dialog.docs.toPlainText() and dialog.help.candidates() == ("", {})
    dialog.help.complete()  # nothing to offer, nothing breaks
    assert dialog.editor.toPlainText() == TEXT


def test_the_docs_panel_follows_the_cursor(qtbot):
    dialog = dialog_with_text(qtbot, TEXT)
    dialog.help.set_schema(DOC, "Deployment", "apps/v1")
    put_cursor(dialog, line_of(TEXT, "replicas: 3"))
    html = dialog.help.docs_html()
    assert "spec.replicas" in html and "integer" in html and "Number of desired pods" in html
    put_cursor(dialog, line_of(TEXT, "image: nginx"))
    assert "spec.template.spec.containers[].image" in dialog.help.docs_html() and "Container image name" in dialog.help.docs_html()
    put_cursor(dialog, line_of(TEXT, "- name: app"))
    assert "required" in dialog.help.docs_html()  # name is required in a container
    put_cursor(dialog, line_of(TEXT, "spec:"))
    assert "Fields:" in dialog.help.docs_html() and "selector</code>*" in dialog.help.docs_html()  # required fields are starred
    dialog.editor.setPlainText(TEXT.replace("replicas: 3", "replicass: 3"))
    put_cursor(dialog, line_of(dialog.editor.toPlainText(), "replicass"))
    assert "Not in this cluster's schema" in dialog.help.docs_html()


def test_unknown_fields_are_named_when_the_text_parses(qtbot):
    dialog = dialog_with_text(qtbot, TEXT)
    dialog.help.set_schema(DOC, "Deployment", "apps/v1")
    assert dialog.fields_status.text() == ""
    dialog.editor.setPlainText(TEXT.replace("replicas: 3", "replicass: 3"))
    qtbot.waitUntil(lambda: "spec.replicass" in dialog.fields_status.text(), timeout=3000)
    dialog.editor.setPlainText("spec: [unclosed")  # not YAML yet: no guess
    qtbot.waitUntil(lambda: dialog.fields_status.text() == "", timeout=3000)


def test_completion_offers_missing_field_names_and_enum_values(qtbot):
    typed = TEXT.replace("      - name: side\n        image: busybox\n", "      - name: side\n        im\n")
    dialog = dialog_with_text(qtbot, typed)
    dialog.help.set_schema(DOC, "Deployment", "apps/v1")
    put_cursor(dialog, typed.splitlines().index("        im"))  # the end of the half-typed key
    partial, offers = dialog.help.candidates()
    assert partial == "im" and any(label.startswith("image  (string") for label in offers) and not any(l.startswith("name") for l in offers)  # name is already there
    assert offers["image  (string)"] == "image: " and offers["args  (array)"].startswith("args:\n") and offers["args  (array)"].endswith("- ")
    typed2 = TEXT.replace("  replicas: 3\n", "  replicas: 3\n  strategy:\n    type: Rec\n")
    dialog.editor.setPlainText(typed2)
    put_cursor(dialog, typed2.splitlines().index("    type: Rec"))
    partial, offers = dialog.help.candidates()
    assert partial == "Rec" and set(offers) == {"Recreate", "RollingUpdate"}


def test_ctrl_space_opens_the_popup_and_choosing_inserts_the_field(qtbot):
    typed = TEXT.replace("      - name: side\n        image: busybox\n", "      - name: side\n        im\n")
    dialog = dialog_with_text(qtbot, typed)
    dialog.show()
    dialog.help.set_schema(DOC, "Deployment", "apps/v1")
    put_cursor(dialog, typed.splitlines().index("        im"))
    QApplication.sendEvent(dialog.editor, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier))
    assert dialog.help.completer.popup().isVisible() and dialog.help.model.stringList() == ["image  (string)"]
    dialog.help._insert("image  (string)")
    assert "        image: \n" in dialog.editor.toPlainText() and "\n        im\n" not in dialog.editor.toPlainText()
    dialog.help.completer.popup().hide()


class Svc:
    def __init__(self, fail=False):
        self.paths, self.fail = [], fail

    def openapi_document(self, path):
        self.paths.append(path)
        if self.fail:
            raise ConnectionError("openapi disabled")
        return DOC


def attach(qtbot, svc, kind="Deployment", manifest=None):
    ws = ClusterWorkspace(svc, "ctx")
    qtbot.addWidget(ws)
    dialog = dialog_with_text(qtbot, TEXT)
    ws._attach_schema(dialog, kind, manifest if manifest is not None else {"metadata": {"name": "web"}})
    return ws, dialog


def test_the_workspace_loads_the_kinds_document_once_and_gives_it_to_every_editor(qtbot):
    svc = Svc()
    ws, dialog = attach(qtbot, svc)
    qtbot.waitUntil(lambda: dialog.help.schema is not None, timeout=5000)
    assert svc.paths == ["/openapi/v3/apis/apps/v1"] and "Schema help from this cluster's OpenAPI" in dialog.help.state
    again = dialog_with_text(qtbot, TEXT)
    ws._attach_schema(again, "Deployment", {})
    assert again.help.schema is not None and svc.paths == ["/openapi/v3/apis/apps/v1"]  # kept: not read again
    ws.shutdown()


def test_the_manifests_api_version_picks_the_document_and_failures_are_explained(qtbot):
    svc = Svc()
    ws, dialog = attach(qtbot, svc, "Gateway", {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "Gateway"})
    qtbot.waitUntil(lambda: bool(svc.paths), timeout=5000)
    assert svc.paths == ["/openapi/v3/apis/gateway.networking.k8s.io/v1"]
    qtbot.waitUntil(lambda: "no schema for Gateway" in dialog.help.state, timeout=5000)  # the fixture document has no Gateway
    ws2, broken = attach(qtbot, Svc(fail=True))
    qtbot.waitUntil(lambda: "openapi disabled" in broken.help.state, timeout=5000)
    ws3, unknown = attach(qtbot, Svc(), kind="Mystery")
    assert "not known" in unknown.help.state
    ws4, nosvc = attach(qtbot, SimpleNamespace())
    assert "cannot read OpenAPI" in nosvc.help.state
    for w in (ws, ws2, ws3, ws4):
        w.shutdown()
