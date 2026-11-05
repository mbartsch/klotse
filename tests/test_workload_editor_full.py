"""The rest of the workload form: pod settings, volumes, ports, probes, mounts, command, security and the sub-tabs."""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.ui import workload_model as wm
from klotse.ui.workload_editor import WorkloadEditDialog

RICH = {
    "apiVersion": "apps/v1", "kind": "Deployment",
    "metadata": {"name": "api", "namespace": "shop", "uid": "u-9", "labels": {"app": "api"}},
    "spec": {
        "replicas": 2, "paused": False, "progressDeadlineSeconds": 600, "selector": {"matchLabels": {"app": "api"}},
        "template": {
            "metadata": {"labels": {"app": "api", "tier": "back"}, "annotations": {"prometheus.io/scrape": "true"}},
            "spec": {
                "serviceAccountName": "api-sa", "priorityClassName": "high", "terminationGracePeriodSeconds": 45,
                "nodeSelector": {"disk": "ssd"}, "affinity": {"podAntiAffinity": {"x": 1}},
                "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "api", "effect": "NoSchedule",
                                 "tolerationSeconds": 60}, {"operator": "Exists"}],
                "imagePullSecrets": [{"name": "regcred"}],
                "securityContext": {"runAsUser": 1000, "fsGroup": 2000, "seccompProfile": {"type": "RuntimeDefault"}},
                "volumes": [{"name": "cache", "emptyDir": {"sizeLimit": "1Gi"}},
                            {"name": "conf", "configMap": {"name": "api-conf", "defaultMode": 420}},
                            {"name": "tok", "secret": {"secretName": "api-tok"}},
                            {"name": "data", "persistentVolumeClaim": {"claimName": "api-data"}},
                            {"name": "host", "hostPath": {"path": "/var/log", "type": "Directory"}},
                            {"name": "proj", "projected": {"sources": []}}],
                "containers": [{
                    "name": "api", "image": "reg/api:1.4", "command": ["/bin/api"], "args": ["--port", "8080"],
                    "envFrom": [{"configMapRef": {"name": "api-env", "optional": True}, "prefix": "X_"}, {"secretRef": {"name": "api-sec"}}],
                    "ports": [{"name": "http", "containerPort": 8080, "protocol": "TCP"}, {"containerPort": 9090}],
                    "volumeMounts": [{"name": "conf", "mountPath": "/etc/api", "readOnly": True, "mountPropagation": "None"},
                                     {"name": "cache", "mountPath": "/cache", "subPath": "a"}],
                    "livenessProbe": {"httpGet": {"path": "/healthz", "port": "http", "httpHeaders": [{"name": "X", "value": "1"}]},
                                      "periodSeconds": 10, "failureThreshold": 3},
                    "readinessProbe": {"tcpSocket": {"port": 8080}, "initialDelaySeconds": 5},
                    "startupProbe": {"exec": {"command": ["cat", "/tmp/ready"]}},
                    "lifecycle": {"preStop": {"exec": {"command": ["sleep", "5"]}}},
                    "securityContext": {"runAsUser": 1001, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}}},
    "status": {"replicas": 2}}


def make(qtbot, raw=None, kind="Deployment"):
    dlg = WorkloadEditDialog(kind, deepcopy(raw or RICH))
    qtbot.addWidget(dlg)
    return dlg


def saved(dlg):
    dlg._save()
    assert dlg.result_body is not None, dlg.hint.text()
    return dlg.result_body


def test_a_rich_manifest_comes_back_unchanged_when_nothing_is_edited(qtbot):
    assert saved(make(qtbot)) == RICH


def test_the_form_has_sections_and_sub_tabs_with_counts(qtbot):
    dlg = make(qtbot)
    assert [dlg.sections.tabText(i) for i in range(3)] == ["Workload", "Pod template", "Containers"]
    form = dlg.forms[0]
    titles = [form.sub.tabText(i) for i in range(form.sub.count())]
    assert titles == ["Image and command", "Resources", "Environment (2)", "Ports (2)", "Probes (3)", "Volume mounts (2)", "Security (1)"]
    pod = [dlg.pod.sub.tabText(i) for i in range(dlg.pod.sub.count())]
    assert pod == ["Identity and scheduling (7)", "Volumes (6)", "Pod labels", "Security"]
    form.ports._remove_all = None
    form.ports.table.selectAll()
    form.ports._remove()
    assert form.sub.tabText(3) == "Ports"  # an empty topic shows no count


def test_pod_settings_edit_and_keep_what_the_form_does_not_show(qtbot):
    dlg = make(qtbot)
    dlg.pod.service_account.setText("other")
    dlg.pod.priority.setText("")
    dlg.pod.grace.setText("10")
    dlg.pod.node_selector.key_box.setEditText("zone")
    dlg.pod.node_selector.value_edit.setText("a")
    dlg.pod.node_selector._add()
    dlg.pod.tolerations.table.item(0, 4).setText("120")
    dlg.pod.pull_secrets._add()
    dlg.pod.pull_secrets.table.item(1, 0).setText("second")
    dlg.pod.ids["runAsUser"].setText("")
    dlg.pod.non_root.setCurrentIndex(1)
    spec = saved(dlg)["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == "other" and "priorityClassName" not in spec and spec["terminationGracePeriodSeconds"] == 10
    assert spec["nodeSelector"] == {"disk": "ssd", "zone": "a"} and spec["tolerations"][0]["tolerationSeconds"] == 120
    assert spec["tolerations"][1] == {"operator": "Exists"}
    assert spec["imagePullSecrets"] == [{"name": "regcred"}, {"name": "second"}]
    assert spec["securityContext"] == {"fsGroup": 2000, "seccompProfile": {"type": "RuntimeDefault"}, "runAsNonRoot": True}
    assert spec["affinity"] == RICH["spec"]["template"]["spec"]["affinity"]  # not in the form: untouched


def test_pod_labels_must_keep_the_selector_labels(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = make(qtbot)
    dlg.pod.template_labels.table.item(0, 1).setText("changed")
    dlg._save()
    assert dlg.result_body is None and "selector" in shown[0] and "app=api" in shown[0]
    dlg.pod.template_labels.table.item(0, 1).setText("api")
    dlg.pod.template_labels.table.item(1, 1).setText("front")
    body = saved(dlg)
    assert body["spec"]["template"]["metadata"]["labels"] == {"app": "api", "tier": "front"}


def test_volumes_edit_add_and_keep_unknown_types(qtbot):
    dlg = make(qtbot)
    vol = dlg.pod.volumes
    names = [vol.table.item(r, 0).text() for r in range(vol.count())]
    assert names == ["cache", "conf", "tok", "data", "host", "proj"]
    assert vol.table.item(5, 1).text() == wm.OTHER_VOLUME
    assert not vol.table.item(1, 1).flags() & vol.table.item(1, 1).flags().ItemIsEditable  # an existing type is fixed
    vol.table.item(1, 2).setText("new-conf")
    vol.table.item(4, 2).setText("/var/data")
    vol._add()
    vol.table.item(6, 0).setText("scratch")
    vol.table.item(6, 1).setText("emptyDir")
    vol.table.item(6, 2).setText("Memory")
    spec = saved(dlg)["spec"]["template"]["spec"]
    by_name = {v["name"]: v for v in spec["volumes"]}
    assert by_name["conf"]["configMap"] == {"name": "new-conf", "defaultMode": 420}
    assert by_name["host"]["hostPath"] == {"path": "/var/data", "type": "Directory"}
    assert by_name["cache"] == {"name": "cache", "emptyDir": {"sizeLimit": "1Gi"}}
    assert by_name["proj"] == {"name": "proj", "projected": {"sources": []}}
    assert by_name["scratch"] == {"name": "scratch", "emptyDir": {"medium": "Memory"}}


def test_a_mount_of_a_missing_volume_blocks_saving(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    dlg = make(qtbot)
    dlg.pod.volumes.table.selectRow(1)  # conf is mounted by the container
    dlg.pod.volumes._remove()
    dlg._save()
    assert dlg.result_body is None and "not one of the pod's volumes" in shown[0]


def test_container_fields_command_ports_mounts_envfrom(qtbot):
    dlg = make(qtbot)
    f = dlg.forms[0]
    f.command.setPlainText("/bin/other\n")
    f.args.setPlainText("")
    f.ports.table.item(1, 1).setText("9091")
    f.ports.table.item(0, 2).setText("UDP")
    f.mounts.table.item(0, 1).setText("/etc/new")
    f.mounts.table.item(1, 3).setCheckState(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Checked)
    f.env_from.table.item(0, 1).setText("renamed")
    f.env_from.table.item(1, 0).setText("ConfigMap")
    c = saved(dlg)["spec"]["template"]["spec"]["containers"][0]
    assert c["command"] == ["/bin/other"] and "args" not in c
    assert c["ports"] == [{"name": "http", "containerPort": 8080, "protocol": "UDP"}, {"containerPort": 9091}]
    assert c["volumeMounts"][0] == {"name": "conf", "mountPath": "/etc/new", "readOnly": True, "mountPropagation": "None"}
    assert c["volumeMounts"][1] == {"name": "cache", "mountPath": "/cache", "subPath": "a", "readOnly": True}
    assert c["envFrom"][0] == {"configMapRef": {"name": "renamed", "optional": True}, "prefix": "X_"}
    assert c["envFrom"][1] == {"configMapRef": {"name": "api-sec"}}
    assert c["lifecycle"] == RICH["spec"]["template"]["spec"]["containers"][0]["lifecycle"]


def test_probes_change_mode_and_keep_their_extras(qtbot):
    dlg = make(qtbot)
    live, ready, start = (dlg.forms[0].probe_editors[k] for k in ("livenessProbe", "readinessProbe", "startupProbe"))
    assert (live.mode.currentText(), ready.mode.currentText(), start.mode.currentText()) == ("HTTP GET", "TCP socket", "Command")
    live.path.setText("/ready")
    live.timings["periodSeconds"].setText("")
    ready.mode.setCurrentText("HTTP GET")
    ready.port.setText("8080")
    ready.path.setText("/up")
    start.mode.setCurrentText("None")
    c = saved(dlg)["spec"]["template"]["spec"]["containers"][0]
    assert c["livenessProbe"] == {"httpGet": {"path": "/ready", "port": "http", "httpHeaders": [{"name": "X", "value": "1"}]},
                                  "failureThreshold": 3}
    assert c["readinessProbe"] == {"httpGet": {"port": 8080, "path": "/up"}, "initialDelaySeconds": 5}
    assert "startupProbe" not in c


def test_a_new_probe_and_its_validation(qtbot):
    dlg = make(qtbot)
    dlg.forms[0].probe_editors["startupProbe"].mode.setCurrentText("gRPC")
    assert wm.validate("Deployment", dlg.values(), dlg._raw) == ["api: startup probe: enter a port"]
    dlg.forms[0].probe_editors["startupProbe"].port.setText("9000")
    dlg.forms[0].probe_editors["startupProbe"].timings["periodSeconds"].setText("x")
    assert "period (s) must be a whole number" in " ".join(wm.validate("Deployment", dlg.values(), dlg._raw))
    dlg.forms[0].probe_editors["startupProbe"].timings["periodSeconds"].setText("5")
    assert wm.validate("Deployment", dlg.values(), dlg._raw) == []
    assert saved(dlg)["spec"]["template"]["spec"]["containers"][0]["startupProbe"] == {"grpc": {"port": 9000}, "periodSeconds": 5}


def test_container_security_keeps_other_keys_and_flags_privileged(qtbot):
    dlg = make(qtbot)
    f = dlg.forms[0]
    f.ids["runAsUser"].setText("")
    f.flags["readOnlyRootFilesystem"].setCurrentIndex(2)
    f.flags["privileged"].setCurrentIndex(1)
    assert "privileged" in dlg.hint.text()
    ctx = saved(dlg)["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert ctx == {"readOnlyRootFilesystem": False, "privileged": True, "capabilities": {"drop": ["ALL"]}}


def test_workload_level_fields_for_deployments(qtbot):
    dlg = make(qtbot)
    assert dlg.progress.text() == "600" and dlg.paused.value() is False
    dlg.progress.setText("")
    dlg.paused.setCurrentIndex(1)
    spec = saved(dlg)["spec"]
    assert "progressDeadlineSeconds" not in spec and spec["paused"] is True


@pytest.mark.parametrize("change, message", [
    (lambda d: d.pod.grace.setText("x"), "Termination grace"),
    (lambda d: d.pod.tolerations.table.item(1, 2).setText("v"), "Exists"),
    (lambda d: d.pod.tolerations.table.item(0, 3).setText("Never"), "effect"),
    (lambda d: d.pod.tolerations.table.item(0, 4).setText("soon"), "seconds"),
    (lambda d: d.pod.volumes.table.item(0, 0).setText("Bad_Name"), "lowercase"),
    (lambda d: d.pod.volumes.table.item(0, 0).setText("conf"), "twice"),
    (lambda d: d.forms[0].ports.table.item(0, 1).setText("70000"), "1 to 65535"),
    (lambda d: d.forms[0].ports.table.item(0, 0).setText("Not Valid"), "port name"),
    (lambda d: d.forms[0].mounts.table.item(0, 1).setText("relative"), "absolute path"),
    (lambda d: d.forms[0].ids["runAsUser"].setText("root"), "runAsUser"),
    (lambda d: d.forms[0].env_from.table.item(1, 1).setText(""), "ConfigMap or Secret name"),
])
def test_validation_blocks_bad_values(qtbot, monkeypatch, change, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot)
    change(dlg)
    assert any(message in p for p in wm.validate("Deployment", dlg.values(), dlg._raw))
    dlg._save()
    assert dlg.result_body is None


def test_a_blank_new_row_is_ignored_and_the_other_kinds_use_the_same_form(qtbot):
    dlg = make(qtbot)
    dlg.forms[0].ports._add()
    dlg.forms[0].ports.table.item(2, 1).setText("")  # the default port number removed: a blank row
    dlg.forms[0].mounts._add()
    assert len(saved(dlg)["spec"]["template"]["spec"]["containers"][0]["ports"]) == 2
    ds = {"metadata": {"name": "agent", "namespace": "ops"}, "spec": {"selector": {"matchLabels": {"a": "b"}},
          "template": {"metadata": {"labels": {"a": "b"}}, "spec": {"containers": [{"name": "x", "image": "i:1"}],
                                                                   "tolerations": [{"operator": "Exists"}]}}}}
    d = make(qtbot, ds, "DaemonSet")
    d.pod.tolerations._add()
    d.pod.tolerations.table.item(1, 0).setText("node-role")
    d.pod.tolerations.table.item(1, 1).setText("Exists")
    out = saved(d)["spec"]["template"]["spec"]["tolerations"]
    assert out == [{"operator": "Exists"}, {"key": "node-role", "operator": "Exists"}]


def test_statefulset_volume_claim_templates_count_as_mountable(qtbot):
    sts = {"metadata": {"name": "db", "namespace": "ops"},
           "spec": {"replicas": 1, "selector": {"matchLabels": {"a": "b"}}, "volumeClaimTemplates": [{"metadata": {"name": "data"}}],
                    "template": {"metadata": {"labels": {"a": "b"}},
                                 "spec": {"containers": [{"name": "d", "image": "pg:16",
                                                          "volumeMounts": [{"name": "data", "mountPath": "/var/lib/pg"}]}]}}}}
    dlg = make(qtbot, sts, "StatefulSet")
    assert saved(dlg) == sts
