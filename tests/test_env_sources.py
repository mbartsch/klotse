"""The Environment table of the workload form: one table, a Source column, and variables from a field, Secret, ConfigMap
or resource that can be added and edited."""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.ui import workload_model as wm
from klotse.ui.workload_editor import WorkloadEditDialog

POD_NS = {"name": "POD_NAMESPACE", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.namespace"}}}
SECRET = {"name": "PW", "valueFrom": {"secretKeyRef": {"name": "db", "key": "pw", "optional": True}}}
LIMIT = {"name": "MEM", "valueFrom": {"resourceFieldRef": {"containerName": "app", "resource": "limits.memory", "divisor": "1Mi"}}}
FUTURE = {"name": "NEW", "valueFrom": {"fileKeyRef": {"path": "x"}}}
DEPLOY = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"},
          "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "web"}},
                   "template": {"metadata": {"labels": {"app": "web"}},
                                "spec": {"containers": [{"name": "app", "image": "nginx:1.25",
                                                         "env": [{"name": "A", "value": "1"}, POD_NS, SECRET, LIMIT, FUTURE],
                                                         "envFrom": [{"configMapRef": {"name": "common", "optional": True}, "prefix": "X_"}]}]}}}}


def make(qtbot):
    dlg = WorkloadEditDialog("Deployment", deepcopy(DEPLOY), namespaces=["shop"])
    qtbot.addWidget(dlg)
    return dlg


def env_of(dlg):
    dlg._save()
    return dlg.result_body["spec"]["template"]["spec"]["containers"][0]


def test_the_source_column_says_what_each_row_is(qtbot):
    dlg = make(qtbot)
    t = dlg.forms[0].env.table
    rows = [(t.item(r, 0).text(), t.item(r, 1).text(), t.item(r, 2).text()) for r in range(t.rowCount())]
    assert rows == [("Value", "A", "1"), ("Field", "POD_NAMESPACE", "metadata.namespace"), ("Secret key", "PW", "db / pw"),
                    ("Resource", "MEM", "limits.memory"), ("Other", "NEW", "another source"),
                    ("ConfigMap, all keys", "common", "X_")]


def test_an_untouched_environment_comes_back_identical(qtbot):
    c = env_of(make(qtbot))
    assert c["env"] == DEPLOY["spec"]["template"]["spec"]["containers"][0]["env"]
    assert c["envFrom"] == DEPLOY["spec"]["template"]["spec"]["containers"][0]["envFrom"]


def test_a_field_variable_can_be_added(qtbot):
    dlg = make(qtbot)
    env = dlg.forms[0].env
    env.add_row("Field")
    row = env.table.rowCount() - 1
    env.table.item(row, 1).setText("POD_IP")
    env.table.item(row, 2).setText("status.podIP")
    assert env_of(dlg)["env"][-1] == {"name": "POD_IP", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "status.podIP"}}}


def test_secret_configmap_and_resource_variables_can_be_added(qtbot):
    dlg = make(qtbot)
    env = dlg.forms[0].env
    for source, name, detail in (("Secret key", "S", "creds / token"), ("ConfigMap key", "C", "cfg/level"), ("Resource", "CPU", "requests.cpu")):
        env.add_row(source)
        row = env.table.rowCount() - 1
        env.table.item(row, 1).setText(name)
        env.table.item(row, 2).setText(detail)
    got = {e["name"]: e for e in env_of(dlg)["env"]}
    assert got["S"]["valueFrom"] == {"secretKeyRef": {"name": "creds", "key": "token"}}
    assert got["C"]["valueFrom"] == {"configMapKeyRef": {"name": "cfg", "key": "level"}}
    assert got["CPU"]["valueFrom"] == {"resourceFieldRef": {"resource": "requests.cpu"}}


def test_editing_a_reference_keeps_the_fields_the_form_does_not_show(qtbot):
    dlg = make(qtbot)
    t = dlg.forms[0].env.table
    t.item(2, 2).setText("db2 / pw2")
    t.item(3, 2).setText("limits.cpu")
    env = {e["name"]: e for e in env_of(dlg)["env"]}
    assert env["PW"]["valueFrom"]["secretKeyRef"] == {"name": "db2", "key": "pw2", "optional": True}
    assert env["MEM"]["valueFrom"]["resourceFieldRef"] == {"containerName": "app", "resource": "limits.cpu", "divisor": "1Mi"}


def test_changing_the_source_of_a_row_changes_its_kind(qtbot):
    dlg = make(qtbot)
    t = dlg.forms[0].env.table
    t.item(0, 0).setText("Field")
    t.item(0, 2).setText("spec.nodeName")
    first = env_of(dlg)["env"][0]
    assert first == {"name": "A", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "spec.nodeName"}}}
    t.item(1, 0).setText("Value")
    t.item(1, 2).setText("fixed")
    assert env_of(dlg)["env"][1] == {"name": "POD_NAMESPACE", "value": "fixed"}


def test_a_source_the_form_does_not_know_is_locked_and_kept(qtbot):
    from PySide6.QtCore import Qt
    dlg = make(qtbot)
    t = dlg.forms[0].env.table
    assert not t.item(4, 0).flags() & Qt.ItemIsEditable and not t.item(4, 2).flags() & Qt.ItemIsEditable


@pytest.mark.parametrize("source, name, detail, message", [
    ("Field", "X", "", "needs a pod field"), ("Secret key", "X", "only-a-name", "name and a key"),
    ("ConfigMap key", "X", "/key", "name and a key"), ("Resource", "X", "", "needs a resource")])
def test_incomplete_references_are_refused(qtbot, monkeypatch, source, name, detail, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot)
    env = dlg.forms[0].env
    env.add_row(source)
    row = env.table.rowCount() - 1
    env.table.item(row, 1).setText(name)
    env.table.item(row, 2).setText(detail)
    assert any(message in p for p in wm.validate("Deployment", dlg.values(), dlg._raw))
    dlg._save()
    assert dlg.result_body is None
