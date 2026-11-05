"""Form editor for Deployments, DaemonSets and StatefulSets: pure helpers, the dialog and the routing from Edit."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s.resources import Row
from klotse.ui import workload_editor as we
from klotse.ui.service_account_editor import LAST_APPLIED
from klotse.ui.workload_editor import WorkloadEditDialog, build_body, validate
from klotse.ui.workspace import ClusterWorkspace

DEPLOYMENT = {
    "apiVersion": "apps/v1", "kind": "Deployment",
    "metadata": {"name": "web", "namespace": "shop", "uid": "u-1", "resourceVersion": "7", "labels": {"app": "web"},
                 "annotations": {LAST_APPLIED: "{huge}", "note": "x"}},
    "spec": {"replicas": 3, "selector": {"matchLabels": {"app": "web"}}, "paused": False,
             "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": 1}},
             "template": {"metadata": {"labels": {"app": "web"}},
                          "spec": {"serviceAccountName": "sa", "initContainers": [{"name": "init", "image": "busybox:1"}],
                                   "containers": [{"name": "app", "image": "nginx:1.25", "imagePullPolicy": "Always",
                                                   "ports": [{"containerPort": 80}],
                                                   "resources": {"requests": {"cpu": "100m"}, "limits": {"cpu": "1", "memory": "1Gi"}},
                                                   "env": [{"name": "A", "value": "1"},
                                                           {"name": "DB", "valueFrom": {"secretKeyRef": {"name": "db", "key": "pw"}}}]}]}}},
    "status": {"replicas": 3}}


def make(qtbot, kind="Deployment", raw=None):
    dlg = WorkloadEditDialog(kind, deepcopy(raw or DEPLOYMENT))
    qtbot.addWidget(dlg)
    return dlg


# ---- pure helpers -----------------------------------------------------------------
@pytest.mark.parametrize("text, ok", [("100m", True), ("0.5", True), ("1", True), ("256Mi", True), ("1Gi", True),
                                      ("", False), ("1 Gi", False), ("abc", False), ("5mi", False), ("-1", False)])
def test_quantities(text, ok):
    assert we.valid_quantity(text) is ok


def test_quantity_values_compare_cores_and_bytes():
    assert we.quantity_value("500m") == pytest.approx(0.5) and we.quantity_value("1") == 1
    assert we.quantity_value("1Gi") == 1024 ** 3 and we.quantity_value("1G") == 10 ** 9 and we.quantity_value("x") is None


def test_counts_and_percentages():
    assert we.valid_count("2") and not we.valid_count("25%") and we.valid_count("25%", percent=True)
    assert not we.valid_count("-1") and not we.valid_count("")
    assert we.number_or_text("2") == 2 and we.number_or_text("25%") == "25%"


def test_env_sources_are_described():
    assert we.env_source({"valueFrom": {"secretKeyRef": {"name": "db", "key": "pw"}}}) == "Secret db, key pw"
    assert "ConfigMap c" in we.env_source({"valueFrom": {"configMapKeyRef": {"name": "c", "key": "k"}}})
    assert we.env_source({"valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}) == "field metadata.name"
    assert we.env_source({"valueFrom": {}}) == "another source"


def test_kube_system_workloads_need_care():
    assert we.workload_warnings("Deployment", {"metadata": {"namespace": "kube-system"}})
    assert we.workload_warnings("Deployment", DEPLOYMENT) == []


# ---- the dialog: values and the manifest it builds ----------------------------------
def test_the_dialog_shows_what_the_manifest_has(qtbot):
    dlg = make(qtbot)
    assert dlg.replicas.value() == 3 and dlg.strategy.currentText() == "RollingUpdate"
    assert dlg.knobs["maxSurge"].text() == "25%" and dlg.knobs["maxUnavailable"].text() == "1"
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["init (init)", "app"]
    app = dlg.forms[1]
    assert app.image.text() == "nginx:1.25" and app.pull.currentText() == "Always"
    assert app.boxes["requests", "cpu"].text() == "100m" and app.boxes["limits", "memory"].text() == "1Gi"
    assert app.env.table.rowCount() == 2 and app.env.table.item(1, 0).text() == "Secret key" and app.env.table.item(1, 2).text() == "db / pw"
    assert dlg.labels.items() == {"app": "web"} and dlg.annotations.items()[LAST_APPLIED] == "{huge}"


def test_saving_without_changes_gives_back_the_same_manifest(qtbot):
    dlg = make(qtbot)
    dlg._save()
    assert dlg.result() == QDialog.Accepted and dlg.result_body == DEPLOYMENT


def test_edits_change_only_their_fields(qtbot):
    dlg = make(qtbot)
    dlg.replicas.setValue(5)
    dlg.forms[1].image.setText("nginx:1.26")
    dlg.forms[1].boxes["requests", "memory"].setText("128Mi")
    dlg.forms[1].boxes["limits", "cpu"].setText("")
    dlg.knobs["maxSurge"].setText("2")
    dlg.labels.key_box.setEditText("tier")
    dlg.labels.value_edit.setText("front")
    dlg.labels._add()
    dlg._save()
    body, spec = dlg.result_body, dlg.result_body["spec"]
    c = spec["template"]["spec"]["containers"][0]
    assert spec["replicas"] == 5 and c["image"] == "nginx:1.26"
    assert c["resources"] == {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "1Gi"}}
    assert spec["strategy"]["rollingUpdate"] == {"maxSurge": 2, "maxUnavailable": 1}
    assert body["metadata"]["labels"] == {"app": "web", "tier": "front"}
    # everything the form does not cover is untouched
    assert c["ports"] == [{"containerPort": 80}] and spec["selector"] == DEPLOYMENT["spec"]["selector"]
    assert spec["template"]["spec"]["serviceAccountName"] == "sa" and body["status"] == DEPLOYMENT["status"]
    assert body["metadata"]["annotations"][LAST_APPLIED] == "{huge}" and body["metadata"]["uid"] == "u-1"
    assert we.template_changed(DEPLOYMENT, body)


def test_a_replica_only_change_does_not_touch_the_pod_template(qtbot):
    dlg = make(qtbot)
    dlg.replicas.setValue(0)
    dlg._save()
    assert dlg.result_body["spec"]["replicas"] == 0 and not we.template_changed(DEPLOYMENT, dlg.result_body)


def test_environment_variables_from_secrets_survive_and_plain_ones_edit(qtbot):
    dlg = make(qtbot)
    env = dlg.forms[1].env
    env.table.item(0, 2).setText("2")
    env.add_row("Value")
    env.table.item(2, 1).setText("NEW")
    env.table.item(2, 2).setText("v")
    dlg._save()
    got = dlg.result_body["spec"]["template"]["spec"]["containers"][0]["env"]
    assert got == [{"name": "A", "value": "2"},
                   {"name": "DB", "valueFrom": {"secretKeyRef": {"name": "db", "key": "pw"}}},
                   {"name": "NEW", "value": "v"}]


def test_removing_the_last_variable_or_resource_removes_the_field(qtbot):
    dlg = make(qtbot)
    app = dlg.forms[1]
    app.env.table.selectAll()
    app.env._remove()
    for key in app.boxes.values():
        key.setText("")
    dlg._save()
    c = dlg.result_body["spec"]["template"]["spec"]["containers"][0]
    assert "env" not in c and "resources" not in c


def test_recreate_drops_the_rolling_update_settings_and_an_unset_pull_policy_is_removed(qtbot):
    dlg = make(qtbot)
    dlg.strategy.setCurrentText("Recreate")
    dlg.forms[1].pull.setCurrentIndex(0)
    dlg._save()
    spec = dlg.result_body["spec"]
    assert spec["strategy"] == {"type": "Recreate"} and "imagePullPolicy" not in spec["template"]["spec"]["containers"][0]
    assert all(not edit.isEnabled() for edit in dlg.knobs.values())


def test_daemon_sets_have_no_replicas_and_statefulsets_a_partition(qtbot):
    ds = {"metadata": {"name": "agent", "namespace": "ops"},
          "spec": {"updateStrategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": "10%"}},
                   "template": {"spec": {"containers": [{"name": "a", "image": "x:1"}]}}}}
    dlg = make(qtbot, "DaemonSet", ds)
    dlg.replicas.setValue(9)
    dlg._save()
    assert "replicas" not in dlg.result_body["spec"] and dlg.result_body["spec"]["updateStrategy"]["rollingUpdate"] == {"maxUnavailable": "10%"}
    sts = {"metadata": {"name": "db", "namespace": "ops"}, "spec": {"replicas": 2, "updateStrategy": {"type": "OnDelete"},
           "template": {"spec": {"containers": [{"name": "d", "image": "pg:16"}]}}}}
    dlg = make(qtbot, "StatefulSet", sts)
    assert dlg.strategy.currentText() == "OnDelete"
    dlg.strategy.setCurrentText("RollingUpdate")
    dlg.knobs["partition"].setText("1")
    dlg._save()
    assert dlg.result_body["spec"]["updateStrategy"] == {"type": "RollingUpdate", "rollingUpdate": {"partition": 1}}
    assert dlg.result_body["spec"]["replicas"] == 2


# ---- validation ---------------------------------------------------------------------
def values(**changes):
    base = {"strategy_type": "RollingUpdate", "knobs": {}, "min_ready": "", "history": "",
            "containers": [{"name": "app", "image": "nginx:1", "requests": {}, "limits": {}, "env": []}],
            "labels_pairs": [], "annotations_pairs": []}
    base.update(changes)
    return base


def test_validation_messages():
    assert validate("Deployment", values()) == []
    assert validate("Deployment", values(knobs={"maxSurge": "x"}))
    assert validate("Deployment", values(knobs={"maxSurge": "25%"})) == []
    assert validate("StatefulSet", values(knobs={"partition": "25%"}))  # a count only
    assert validate("Deployment", values(min_ready="-1")) and validate("Deployment", values(history="a"))
    bad = values(containers=[{"name": "app", "image": " ", "requests": {"cpu": "2", "memory": "x"}, "limits": {"cpu": "1"},
                              "env": [{"name": "1bad"}, {"name": "A"}, {"name": "A"}]}])
    text = " | ".join(validate("Deployment", bad))
    for part in ("enter an image", "not a valid quantity", "request is higher than the limit", "not a valid variable name",
                 "listed twice"):
        assert part in text
    assert validate("Deployment", values(strategy_type="Recreate", knobs={"maxSurge": "junk"})) == []  # not used


def test_an_invalid_form_does_not_save(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = make(qtbot)
    dlg.forms[1].image.setText("")
    dlg._save()
    assert dlg.result_body is None and shown and "enter an image" in shown[0]
    assert "enter an image" in dlg.hint.text()


def test_images_without_a_fixed_tag_get_a_hint(qtbot):
    dlg = make(qtbot)
    dlg.forms[1].image.setText("registry.local:5000/team/app")
    assert "no fixed tag" in dlg.hint.text()
    dlg.forms[1].image.setText("registry.local:5000/team/app:1.2")
    dlg._hint()
    assert dlg.hint.text() == ""
    dlg.forms[1].image.setText("nginx:latest")
    dlg._hint()
    assert "no fixed tag" in dlg.hint.text()


def test_kube_system_asks_before_saving(qtbot, monkeypatch):
    raw = deepcopy(DEPLOYMENT)
    raw["metadata"]["namespace"] = "kube-system"
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.No)
    dlg = make(qtbot, raw=raw)
    assert dlg.warnings
    dlg._save()
    assert asked and dlg.result_body is None


def test_names_in_the_header_are_escaped(qtbot):
    raw = deepcopy(DEPLOYMENT)
    raw["metadata"]["name"] = "<b>x</b>"
    dlg = make(qtbot, raw=raw)
    assert "<b>x</b>" not in dlg.head.text()


def test_edit_as_yaml_rejects_and_flags_it(qtbot):
    dlg = make(qtbot)
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.result_body is None and dlg.result() == QDialog.Rejected


def test_build_body_does_not_change_its_input(qtbot):
    raw = deepcopy(DEPLOYMENT)
    v = make(qtbot).values()
    v["replicas"], v["strategy_type"] = 9, "Recreate"
    build_body("Deployment", raw, v)
    assert raw == DEPLOYMENT


# ---- routing from the Edit action ------------------------------------------------------
@pytest.mark.parametrize("kind", ["Deployment", "DaemonSet", "StatefulSet"])
def test_workspace_edits_workloads_with_the_form(qtbot, monkeypatch, kind):
    saved, seen = [], []
    ws = ClusterWorkspace(SimpleNamespace(replace=lambda k, body: saved.append((k, body)), list_secret_names=lambda ns: [], list_configmap_names=lambda ns: []), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None

    class FakeForm:
        def __init__(self, k, raw, parent=None, **_kw):
            seen.append(k)
            self.result_body, self.wants_yaml = {"metadata": {"name": "web"}}, False

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("klotse.ui.workspace.WorkloadEditDialog", FakeForm)
    ws.edit_resource(kind, Row("web", "shop", DEPLOYMENT))
    qtbot.waitUntil(lambda: bool(saved), timeout=3000)
    assert seen == [kind] and saved == [(kind, {"metadata": {"name": "web"}})]


def test_edit_as_yaml_from_the_workload_form_opens_the_yaml_editor(qtbot, monkeypatch):
    ws = ClusterWorkspace(SimpleNamespace(list_secret_names=lambda ns: [], list_configmap_names=lambda ns: []), "ctx")
    qtbot.addWidget(ws)
    opened = []

    class Form:
        def __init__(self, *a, **k):
            self.wants_yaml, self.result_body = True, None

        def exec(self):
            return QDialog.Rejected

        def deleteLater(self):
            pass

    class Yaml:
        Accepted = 1

        def __init__(self, title, raw, parent, note="", **_kw):
            opened.append(title)

        def exec(self):
            return 0

    monkeypatch.setattr("klotse.ui.workspace.WorkloadEditDialog", Form)
    monkeypatch.setattr("klotse.ui.workspace.EditDialog", Yaml)
    ws.edit_resource("Deployment", Row("web", "shop", DEPLOYMENT))
    assert opened == ["Edit Deployment web"]
