from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.selectors import cronjob_job_uids, cronjob_pod_info
from klotse.ui.resource_table import ResourceTable
from klotse.ui.workspace import ClusterWorkspace


def owned(kind, name, uid):
    return {"kind": kind, "name": name, "uid": uid, "controller": True}


def job(name="backup-1", uid="job-1", owner_uid="cron-1", namespace="shop"):
    return {"metadata": {"name": name, "uid": uid, "namespace": namespace,
                         "ownerReferences": [owned("CronJob", "backup", owner_uid)]}}


def pod(name="backup-1-pod", uid="job-1", phase="Succeeded", namespace="shop"):
    return {"metadata": {"name": name, "namespace": namespace, "ownerReferences": [owned("Job", "backup-1", uid)]},
            "spec": {"containers": [{"name": "backup"}, {"name": "sidecar"}]}, "status": {"phase": phase}}


def row(uid="cron-1"):
    return SimpleNamespace(name="backup", namespace="shop", raw={"metadata": {"uid": uid}})


def test_owner_uids_exclude_previous_cronjob_and_other_namespaces():
    jobs = [job(), job("backup-2", "job-2"), job("old", "job-old", "old-cron"),
            job("other-ns", "job-other", namespace="other")]
    uids = cronjob_job_uids("shop", "backup", "cron-1", jobs)
    assert uids == {"job-1", "job-2"}
    pods = [pod(), pod("failed", "job-2", "Failed"), pod("live", "job-2", "Running"),
            pod("old", "job-old"), pod("wrong-ns", namespace="other"), pod("unrelated", "unrelated")]
    assert cronjob_pod_info("shop", uids, pods) == [
        ("backup-1-pod", ["backup", "sidecar"], "Succeeded"),
        ("failed", ["backup", "sidecar"], "Failed"),
        ("live", ["backup", "sidecar"], "Running")]


def test_missing_cronjob_identity_is_refused_before_any_list():
    with pytest.raises(ValueError, match="refresh"):
        cronjob_job_uids("shop", "backup", "", iter(()))


def test_service_uses_namespaced_paginated_reads_and_owner_uids():
    service = KubeService.__new__(KubeService)
    seen = []
    service._resource_items = lambda key, ns: seen.append((key, ns)) or [job()]

    def read(ns, **kw):
        seen.append(("pods", ns, kw))
        return SimpleNamespace(items=[pod()], metadata=None)

    service._core = SimpleNamespace(list_namespaced_pod=read)
    assert service.cronjob_pods("shop", "backup", "cron-1") == [("backup-1-pod", ["backup", "sidecar"], "Succeeded")]
    assert seen[0] == ("jobs", "shop") and seen[1][:2] == ("pods", "shop")
    assert seen[1][2]["limit"] > 0


def test_no_jobs_does_not_list_pods_and_permission_errors_propagate():
    service = KubeService.__new__(KubeService)
    service._resource_items = lambda *_: []
    assert service.cronjob_pods("shop", "backup", "cron-1") == []

    def refused(*_):
        raise RuntimeError("403 Forbidden: jobs")

    service._resource_items = refused
    with pytest.raises(RuntimeError, match="403"):
        service.cronjob_pods("shop", "backup", "cron-1")


def sync_run(fn, result, *args, on_error=None):
    try:
        value = fn(*args)
    except Exception as exc:
        if on_error:
            on_error(str(exc))
        else:
            raise
    else:
        result(value)


def test_cronjob_action_reuses_log_view_with_owner_resolver(qtbot):
    targets = [("done-pod", ["app", "sidecar"], "Succeeded")]
    calls = []
    ws = ClusterWorkspace(SimpleNamespace(cronjob_pods=lambda *args: calls.append(args) or targets), "ctx")
    qtbot.addWidget(ws)
    ws._run = sync_run
    opened = []
    ws.open_multi_logs = lambda *args, **kwargs: opened.append((args, kwargs))
    ws.multi_logs("CronJob", row())
    assert calls == [("shop", "backup", "cron-1")]
    args, options = opened[0]
    assert args == ("CronJob backup", "shop")
    assert options["resource_uid"] == "cron-1"
    resolver = options["resolver"]
    assert resolver("") == [("done-pod", ["app", "sidecar"])]
    targets[:] = [("next-run", ["app"], "Running")]
    assert resolver("") == [("next-run", ["app"])]
    assert len(calls) == 2  # following reads ownership again rather than using a fixed snapshot
    ws.shutdown()


def test_cronjob_logs_explain_empty_pods_and_refused_lists(qtbot, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: told.append(args[2]))
    service = SimpleNamespace(cronjob_pods=lambda *_: [])
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws._run = sync_run
    ws.multi_logs("CronJob", row())
    assert "No pods remain" in told[-1]

    def refused(*_):
        raise RuntimeError("403 Forbidden")

    service.cronjob_pods = refused
    ws.multi_logs("CronJob", row())
    assert "list Jobs and Pods" in told[-1] and "403 Forbidden" in told[-1]
    assert ws.log_tabs.count() == 0
    ws.shutdown()


def test_cronjob_offers_visible_logs_action(qtbot):
    table = ResourceTable([("Name", "name")], "CronJob")
    qtbot.addWidget(table)
    seen = []
    table.multi_logs_requested.connect(lambda kind, selected: seen.append((kind, selected)))
    action = next(a for a in table.row_actions(row()) if a.key == "multi-logs")
    assert action.primary
    selected = row()
    next(a for a in table.row_actions(selected) if a.key == "multi-logs").callback()
    assert seen == [("CronJob", selected)]
