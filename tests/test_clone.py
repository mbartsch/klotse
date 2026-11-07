from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog

from klotse.k8s.clone import COPIED_FROM, clone_manifest, copy_name
from klotse.ui.workload_editor import WorkloadEditDialog
from klotse.ui.workspace import ClusterWorkspace

POD = {"apiVersion": "v1", "kind": "Pod",
       "metadata": {"name": "web-7d9f-abcde", "namespace": "shop", "uid": "u", "resourceVersion": "5", "creationTimestamp": "t",
                    "labels": {"app": "web", "pod-template-hash": "7d9f"}, "ownerReferences": [{"kind": "ReplicaSet", "name": "web-7d9f"}],
                    "managedFields": [{}], "annotations": {"kubectl.kubernetes.io/last-applied-configuration": "{}", "keep": "me"}},
       "spec": {"nodeName": "n1", "serviceAccount": "default", "priority": 0,
                "containers": [{"name": "app", "image": "nginx",
                                "volumeMounts": [{"name": "kube-api-access-x", "mountPath": "/var/run/secrets"},
                                                 {"name": "data", "mountPath": "/data"}]}],
                "volumes": [{"name": "kube-api-access-x", "projected": {}}, {"name": "data", "emptyDir": {}}]},
       "status": {"phase": "Running"}}


def deployment(replicas=5):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "api", "namespace": "shop", "uid": "u", "generation": 3,
                         "annotations": {"deployment.kubernetes.io/revision": "4"}},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": "api"}},
                     "template": {"metadata": {"labels": {"app": "api", "pod-template-hash": "x", "tier": "be"}},
                                  "spec": {"nodeName": "n2", "containers": [{"name": "c", "image": "x"}]}}},
            "status": {"readyReplicas": 5}}


def test_pod_copy_is_clean_unlabelled_and_marked():
    body, notes = clone_manifest("Pod", POD)
    meta, spec = body["metadata"], body["spec"]
    assert meta["name"] == "web-7d9f-abcde-copy" and meta["namespace"] == "shop"
    for gone in ("uid", "resourceVersion", "creationTimestamp", "ownerReferences", "managedFields", "labels"):
        assert gone not in meta
    assert meta["annotations"] == {"keep": "me", COPIED_FROM: "web-7d9f-abcde"} and "status" not in body
    assert "nodeName" not in spec and "serviceAccount" not in spec and "priority" not in spec
    assert [v["name"] for v in spec["volumes"]] == ["data"]
    assert spec["containers"][0]["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    assert any("labels removed" in n for n in notes)
    assert POD["metadata"]["uid"] == "u" and POD["spec"]["nodeName"] == "n1"  # the original is untouched


def test_deployment_copy_runs_one_replica_without_controller_labels():
    body, notes = clone_manifest("Deployment", deployment())
    assert body["spec"]["replicas"] == 1 and "status" not in body and "generation" not in body["metadata"]
    labels = body["spec"]["template"]["metadata"]["labels"]
    assert labels == {"app": "api", "tier": "be"} and "nodeName" not in body["spec"]["template"]["spec"]
    assert "annotations" not in body["metadata"]
    assert any("replicas set to 1" in n for n in notes) and any("Service may send" in n for n in notes)
    assert clone_manifest("Deployment", deployment(1))[0]["spec"]["replicas"] == 1


def test_job_copy_drops_the_generated_selector_and_cronjob_copy_is_suspended():
    job = {"kind": "Job", "metadata": {"name": "j"}, "spec": {"selector": {"matchLabels": {"controller-uid": "x"}}, "manualSelector": True,
           "template": {"metadata": {"labels": {"controller-uid": "x", "job-name": "j", "a": "b"}}, "spec": {"containers": []}}}}
    body, _ = clone_manifest("Job", job)
    assert "selector" not in body["spec"] and "manualSelector" not in body["spec"]
    assert body["spec"]["template"]["metadata"]["labels"] == {"a": "b"}
    cron = {"kind": "CronJob", "metadata": {"name": "nightly"}, "spec": {"schedule": "* * * * *", "suspend": False,
            "jobTemplate": {"spec": {"template": {"metadata": {"labels": {"job-name": "j"}}, "spec": {"containers": []}}}}}}
    body, notes = clone_manifest("CronJob", cron)
    assert body["spec"]["suspend"] is True and any("suspended" in n for n in notes)
    assert body["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"] == {}


def test_names_stay_valid_and_other_kinds_are_refused():
    assert copy_name("a" * 63, "Pod").endswith("-copy") and len(copy_name("a" * 63, "Pod")) <= 63
    assert len(copy_name("a" * 52, "CronJob")) <= 52
    with pytest.raises(ValueError):
        clone_manifest("Secret", {})


def test_the_form_opens_prefilled_and_create_sends_the_copy(qtbot, monkeypatch):
    created = []
    ws = ClusterWorkspace(SimpleNamespace(create=lambda kind, body: created.append((kind, body)), list_secret_names=lambda ns: [],
                                         list_configmap_names=lambda ns: []), "ctx")
    qtbot.addWidget(ws)
    seen = {}

    def exec_(self):
        seen["name"] = self.name_edit.text()
        seen["title"] = self.windowTitle()
        self.result_body = self.pending_body()
        return QDialog.Accepted
    monkeypatch.setattr(WorkloadEditDialog, "exec", exec_)
    ws.copy_resource("Pod", SimpleNamespace(name="web-7d9f-abcde", namespace="shop", raw=POD))
    assert seen == {"name": "web-7d9f-abcde-copy", "title": "New Pod"}
    qtbot.waitUntil(lambda: bool(created))
    kind, body = created[0]
    assert kind == "Pod" and body["metadata"]["name"] == "web-7d9f-abcde-copy" and "nodeName" not in body["spec"]
    qtbot.waitUntil(lambda: not ws._workers)


def test_read_only_refuses_the_copy(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    opened = []
    monkeypatch.setattr(WorkloadEditDialog, "exec", lambda self: opened.append(1) or QDialog.Rejected)
    ws.set_read_only(True)
    ws.copy_resource("Pod", SimpleNamespace(name="p", namespace="n", raw=POD))
    assert opened == []
