"""#503: deleting a CustomResourceDefinition counts the objects it takes with it, asks once with that count, then shows
them in a scrollable list and asks again. Bulk delete of CRDs is refused."""
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from klotse.ui.crd_delete_dialog import CrdDeleteDialog
from klotse.ui.workspace import ClusterWorkspace


def crd_row():
    raw = {"metadata": {"name": "certificates.cert-manager.io", "uid": "u-crd"},
           "spec": {"group": "cert-manager.io", "scope": "Namespaced",
                    "names": {"kind": "Certificate", "plural": "certificates"},
                    "versions": [{"name": "v1", "served": True, "storage": True}]}}
    return NS(name="certificates.cert-manager.io", namespace="", raw=raw)


def obj(namespace, name):
    return NS(namespace=namespace, name=name, raw={"metadata": {"creationTimestamp": "2026-11-01T10:00:00Z"}})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(NS(**service), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    return ws


def test_the_list_dialog_names_every_object_and_scrolls(qtbot):
    dialog = CrdDeleteDialog("Certificate", "certificates.cert-manager.io",
                             [("shop", "web-tls", "t"), ("data", "db-tls", "t"), ("", "cluster-thing", "t")])
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 3
    assert [dialog.table.item(r, 0).text() for r in range(3)] == ["shop", "data", "(cluster-scoped)"]
    assert dialog.table.item(1, 1).text() == "db-tls"
    assert "3" in dialog.summary.text() and "every namespace" in dialog.summary.text()
    assert dialog.table.verticalScrollBar() is not None
    assert dialog.delete_button.isDefault() is False and dialog.buttons.button(QDialogButtonBox.Cancel).isDefault()


def test_two_questions_and_the_first_no_deletes_nothing(qtbot, monkeypatch):
    deleted = []
    ws = make_ws(qtbot, delete_resource=lambda *a: deleted.append(a))
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    ws._confirm_crd_delete(crd_row(), "Certificate", [obj("shop", "web-tls"), obj("data", "db-tls")])
    assert asked and "deletes all 2 Certificate objects" in asked[0]
    assert deleted == []


def test_the_second_question_must_also_be_accepted(qtbot, monkeypatch):
    deleted = []
    ws = make_ws(qtbot, delete_resource=lambda *a: deleted.append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(CrdDeleteDialog, "exec", lambda self: QDialog.Rejected)
    ws._confirm_crd_delete(crd_row(), "Certificate", [obj("shop", "web-tls")])
    assert deleted == []
    monkeypatch.setattr(CrdDeleteDialog, "exec", lambda self: QDialog.Accepted)
    ws._run = lambda fn, on_result, *args, on_error=None: on_result(fn(*args))
    ws._deleted = lambda kind, name: None
    ws._confirm_crd_delete(crd_row(), "Certificate", [obj("shop", "web-tls")])
    assert deleted == [("CustomResourceDefinition", "certificates.cert-manager.io", "", "u-crd")]


def test_bulk_delete_of_crds_is_refused(qtbot, monkeypatch):
    ws = make_ws(qtbot, delete_resource=lambda *a: None)
    errors = []
    ws._on_error = errors.append
    ws.bulk_action("CustomResourceDefinition", "delete", [crd_row(), crd_row()])
    assert errors and "one at a time" in errors[0]
