"""The formatted Details page for workloads: kind detection, status line, the facts shown, escaping, the panel switch."""
from types import SimpleNamespace

import pytest

from klotse.ui import workload_view as wv
from klotse.ui.detail_panel import DetailPanel

CONTAINER = {"name": "web", "image": "reg/web:1.2", "command": ["run"], "args": ["--x"],
             "ports": [{"containerPort": 8080, "name": "http"}],
             "resources": {"requests": {"cpu": "100m"}, "limits": {"memory": "256Mi"}},
             "livenessProbe": {"httpGet": {"path": "/healthz", "port": 8080}, "periodSeconds": 10},
             "env": [{"name": "MODE", "value": "prod"},
                     {"name": "PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "pw"}}},
                     {"name": "NODE", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}}],
             "envFrom": [{"configMapRef": {"name": "common"}}],
             "volumeMounts": [{"name": "data", "mountPath": "/data", "readOnly": True}]}
DEPLOY = {"metadata": {"name": "shop", "namespace": "prod", "creationTimestamp": "2026-01-02T03:04:05Z",
                       "labels": {"app": "shop"}, "annotations": {"note": "x" * 500, wv.LAST_APPLIED: "{}"}},
          "spec": {"replicas": 3, "selector": {"matchLabels": {"app": "shop"}},
                   "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": "25%", "maxUnavailable": 0}},
                   "progressDeadlineSeconds": 600,
                   "template": {"spec": {"containers": [CONTAINER], "serviceAccountName": "shop-sa",
                                         "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "shop-data"}}],
                                         "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "web", "effect": "NoSchedule"}]}}},
          "status": {"readyReplicas": 2, "updatedReplicas": 3, "availableReplicas": 2,
                     "conditions": [{"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
                                    {"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate", "message": "quota"}]}}
POD = {"metadata": {"name": "web-1", "namespace": "prod", "ownerReferences": [{"kind": "ReplicaSet", "name": "web-abc"}]},
       "spec": {"containers": [CONTAINER], "initContainers": [{"name": "init", "image": "busybox"}], "nodeName": "n1"},
       "status": {"phase": "Running", "podIP": "10.0.0.5", "qosClass": "Burstable",
                  "containerStatuses": [{"name": "web", "ready": False, "restartCount": 4,
                                         "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                         "lastState": {"terminated": {"reason": "Error", "exitCode": 1}}}]}}
JOB = {"metadata": {"name": "mig"}, "spec": {"completions": 3, "backoffLimit": 2, "template": {"spec": {"containers": [CONTAINER]}}},
       "status": {"succeeded": 3, "conditions": [{"type": "Complete", "status": "True"}]}}
CRON = {"metadata": {"name": "nightly"},
        "spec": {"schedule": "0 3 * * *", "suspend": True, "jobTemplate": {"spec": {"template": {"spec": {"containers": [CONTAINER]}}}}},
        "status": {}}
DS = {"metadata": {"name": "agent"}, "spec": {"updateStrategy": {"type": "RollingUpdate"}, "template": {"spec": {"containers": []}}},
      "status": {"desiredNumberScheduled": 4, "numberReady": 4}}
STS = {"metadata": {"name": "db"}, "spec": {"serviceName": "db", "replicas": 2, "template": {"spec": {"containers": []}}}, "status": {}}
RS = {"metadata": {"name": "web-abc"}, "spec": {"replicas": 1, "template": {"spec": {"containers": []}}}, "status": {"readyReplicas": 1}}


@pytest.mark.parametrize("raw, kind", [(DEPLOY, "Deployment"), (POD, "Pod"), (JOB, "Job"), (CRON, "CronJob"), (DS, "DaemonSet"),
                                       (STS, "StatefulSet"), (RS, "ReplicaSet"), ({"kind": "Pod"}, "Pod"),
                                       ({"spec": {"ports": []}}, None), ({"metadata": {}}, None), ("x", None)])
def test_the_kind_is_found_from_the_manifest(raw, kind):
    assert wv.kind_of(raw) == kind


@pytest.mark.parametrize("kind, raw, text, tone", [
    ("Deployment", DEPLOY, "2 of 3 ready", "warn"), ("Pod", POD, "CrashLoopBackOff", "bad"),
    ("Job", JOB, "Complete", "ok"), ("CronJob", CRON, "Suspended", "warn"), ("DaemonSet", DS, "4 of 4 ready", "ok"),
    ("StatefulSet", STS, "0 of 2 ready", "bad"), ("ReplicaSet", RS, "1 of 1 ready", "ok")])
def test_the_status_line_says_how_the_workload_is_doing(kind, raw, text, tone):
    assert wv.status_line(kind, raw) == (text, tone)


def test_a_deployment_page_shows_the_facts():
    page = wv.render("Deployment", DEPLOY)
    for needle in ("shop", "prod", "2026-01-02 03:04 UTC", "RollingUpdate", "max surge 25%", "app=shop", "shop-sa",
                   "reg/web:1.2", "8080/TCP (http)", "cpu 100m", "memory 256Mi", "HTTP GET http://:8080/healthz",
                   "shop-data", "dedicated", "MinimumReplicasAvailable", "FailedCreate"):
        assert needle in page, needle
    assert wv.LAST_APPLIED not in page  # the long last-applied annotation is left out
    assert "x" * 300 not in page  # a long annotation is cut


def test_secret_references_show_the_reference_never_a_value():
    page = wv.render("Deployment", DEPLOY)
    assert "secret db / pw" in page and "field spec.nodeName" in page and "all keys from config map" in page
    assert "MODE" in page and "prod" in page


def test_a_pod_page_shows_container_state_owner_and_init_containers():
    page = wv.render("Pod", POD)
    for needle in ("ReplicaSet", "web-abc", "10.0.0.5", "Burstable", "CrashLoopBackOff", "4 restarts", "not ready",
                   "init container", "Last exit", "Error (exit 1)", "Containers (2)"):
        assert needle in page, needle


def test_job_and_cronjob_pages():
    assert "3 done of 3" in wv.render("Job", JOB) and "Retries allowed" in wv.render("Job", JOB)
    cron = wv.render("CronJob", CRON)
    assert "0 3 * * *" in cron and "never" in cron and "web" in cron  # the container comes from the job template


def test_everything_from_the_cluster_is_escaped():
    bad = {"metadata": {"name": "<img src=x onerror=1>", "labels": {"<b>k": "<script>"}},
           "spec": {"containers": [{"name": "<i>c</i>", "image": "<u>i</u>", "env": [{"name": "<e>", "value": "<v>"}]}]},
           "status": {"phase": "<p>"}}
    page = wv.render("Pod", bad)
    assert "<img" not in page and "<script>" not in page and "<u>i</u>" not in page and "<e>" not in page
    assert "&lt;img" in page and "&lt;script&gt;" in page


def test_odd_manifests_do_not_break_the_page():
    for kind in wv.WORKLOAD_KINDS:
        assert "<div" in wv.render(kind, {})
        assert "<div" in wv.render(kind, {"metadata": {"name": "x"}, "spec": {"template": None}, "status": None} if kind != "Pod" else {"spec": {}})


def test_other_kinds_have_no_formatted_page():
    with pytest.raises(ValueError):
        wv.render("Service", {})


# ---- the panel ---------------------------------------------------------------------------------------------------
def row(raw, **extra):
    return SimpleNamespace(raw=raw, name=raw["metadata"]["name"], secret_data={}, **extra)


def test_the_panel_opens_workloads_formatted_with_a_yaml_switch(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(row(DEPLOY))
    assert panel.view_bar.isVisible() and panel.formatted.isVisible() and not panel.editor.isVisible()
    assert "shop" in panel.formatted.toPlainText() and not panel.yaml_tools.isVisible()
    panel.yaml_button.click()
    assert panel.editor.isVisible() and panel.yaml_tools.isVisible() and "replicas: 3" in panel.toPlainText()


def test_a_kind_without_a_formatted_page_shows_yaml_and_tools(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(row({"metadata": {"name": "cm"}, "data": {"a": "1"}}))
    assert not panel.view_bar.isVisible() and panel.editor.isVisible() and panel.yaml_tools.isVisible()


def test_find_copy_and_wrap_in_the_yaml_view(qtbot):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QPlainTextEdit
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(row({"metadata": {"name": "cm"}, "data": {"alpha": "1", "beta": "2"}}))
    panel.find_edit.setText("beta")
    assert panel.editor.textCursor().selectedText() == "beta"
    panel.copy_button.click()
    assert "alpha" in QGuiApplication.clipboard().text()
    panel.wrap_box.setChecked(True)
    assert panel.editor.lineWrapMode() == QPlainTextEdit.WidgetWidth


def test_the_yaml_highlighter_colours_keys_and_comments(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(row({"metadata": {"name": "cm"}, "data": {"alpha": 1}}))
    panel.editor.setPlainText("key: 5  # note\nname: value\n")
    block = panel.editor.document().firstBlock()
    colors = {f.start: f.format.foreground().color().name() for f in block.layout().formats()}
    assert 0 in colors  # the key is coloured
    assert any(f.start >= 8 for f in block.layout().formats())  # the comment is coloured
