"""Job and CronJob in the workload form: what a Job allows to change, the schedule, creating, and the service call."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui import workload_model as wm
from klotse.ui.workload_editor import WorkloadEditDialog
from klotse.ui.workspace import ClusterWorkspace

JOB = {"apiVersion": "batch/v1", "kind": "Job",
       "metadata": {"name": "migrate", "namespace": "shop", "uid": "u", "labels": {"job-name": "migrate"}},
       "spec": {"parallelism": 1, "completions": 3, "backoffLimit": 6, "ttlSecondsAfterFinished": 3600,
                "completionMode": "NonIndexed", "suspend": False,
                "selector": {"matchLabels": {"controller-uid": "abc"}},
                "template": {"metadata": {"labels": {"controller-uid": "abc", "job-name": "migrate"}},
                             "spec": {"restartPolicy": "Never",
                                      "containers": [{"name": "m", "image": "reg/migrate:3", "command": ["run"]}]}}},
       "status": {"succeeded": 1}}
CRON = {"apiVersion": "batch/v1", "kind": "CronJob",
        "metadata": {"name": "nightly", "namespace": "shop", "uid": "u2"},
        "spec": {"schedule": "0 3 * * *", "timeZone": "Europe/Berlin", "concurrencyPolicy": "Forbid", "suspend": False,
                 "startingDeadlineSeconds": 120, "successfulJobsHistoryLimit": 3, "failedJobsHistoryLimit": 1,
                 "jobTemplate": {"spec": {"backoffLimit": 2, "ttlSecondsAfterFinished": 600,
                                          "template": {"spec": {"restartPolicy": "OnFailure",
                                                                "containers": [{"name": "c", "image": "reg/job:1"}]}}}}}}


def make(qtbot, kind, raw, create=False):
    dlg = WorkloadEditDialog(kind, deepcopy(raw), create=create, namespaces=["shop"])
    qtbot.addWidget(dlg)
    return dlg


# ---- helpers --------------------------------------------------------------------------------
@pytest.mark.parametrize("text, ok", [("0 3 * * *", True), ("*/5 * * * *", True), ("0 0 1,15 * MON-FRI", True),
                                      ("@hourly", True), ("@daily", True), ("0 3 * *", False), ("", False),
                                      ("every day", False), ("@sometimes", False), ("0 3 * * * *", False)])
def test_schedules(text, ok):
    assert wm.valid_schedule(text) is ok


def test_the_template_and_job_spec_are_found_for_each_kind():
    assert wm.template_of(JOB, "Job")["spec"]["restartPolicy"] == "Never"
    assert wm.job_spec_of(CRON, "CronJob")["backoffLimit"] == 2
    assert wm.template_of(CRON, "CronJob")["spec"]["containers"][0]["name"] == "c"
    assert [c["name"] for _f, _i, c in wm.container_lists(CRON, "CronJob")] == ["c"]
    assert wm.job_spec_of(JOB, "Job") is JOB["spec"]


# ---- a Job that exists ---------------------------------------------------------------------------
def test_an_untouched_job_and_cronjob_come_back_identical(qtbot):
    for kind, raw in (("Job", JOB), ("CronJob", CRON)):
        dlg = make(qtbot, kind, raw)
        dlg._save()
        assert dlg.result_body == raw, kind


def test_a_job_template_is_shown_but_locked(qtbot):
    dlg = make(qtbot, "Job", JOB)
    assert dlg.running and "cannot be changed once it exists" in dlg.head.text()
    form = dlg.forms[0]
    assert not form.image.isEnabled() and not form.pull.isEnabled() and not dlg.pod.tolerations.isEnabled()
    assert not dlg.pod.restart.isEnabled() and dlg.job_edits["parallelism"].isEnabled()
    assert dlg.job_edits["completions"].text() == "3" and dlg.completion_mode.currentText() == "NonIndexed"


def test_a_job_changes_only_what_kubernetes_allows(qtbot):
    dlg = make(qtbot, "Job", JOB)
    dlg.job_edits["parallelism"].setText("3")
    dlg.job_edits["active_deadline"].setText("900")
    dlg.job_edits["ttl"].setText("")
    dlg.suspend.setCurrentIndex(1)
    dlg._save()
    spec = dlg.result_body["spec"]
    assert spec["parallelism"] == 3 and spec["activeDeadlineSeconds"] == 900 and spec["suspend"] is True
    assert "ttlSecondsAfterFinished" not in spec
    assert spec["completions"] == 3 and spec["backoffLimit"] == 6 and spec["template"] == JOB["spec"]["template"]
    assert spec["selector"] == JOB["spec"]["selector"] and dlg.result_body["status"] == JOB["status"]


# ---- a CronJob -----------------------------------------------------------------------------------------
def test_a_cronjob_shows_its_schedule_and_job_settings(qtbot):
    dlg = make(qtbot, "CronJob", CRON)
    assert not dlg.running and dlg.schedule.text() == "0 3 * * *" and dlg.time_zone.text() == "Europe/Berlin"
    assert dlg.concurrency.currentText() == "Forbid" and dlg.cron_edits["success_history"].text() == "3"
    assert dlg.job_edits["backoff"].text() == "2" and dlg.job_edits["ttl"].text() == "600"
    assert "from now on" in dlg.head.text()
    assert dlg.forms[0].image.isEnabled() and dlg.pod.restart.currentText() == "OnFailure"


def test_a_cronjob_edit_reaches_the_schedule_job_settings_and_template(qtbot):
    dlg = make(qtbot, "CronJob", CRON)
    dlg.schedule.setText("*/10 * * * *")
    dlg.time_zone.setText("")
    dlg.concurrency.setCurrentText("Replace")
    dlg.cron_edits["failure_history"].setText("5")
    dlg.job_edits["backoff"].setText("4")
    dlg.job_edits["parallelism"].setText("2")
    dlg.suspend.setCurrentIndex(1)
    dlg.forms[0].image.setText("reg/job:2")
    dlg.pod.restart.setCurrentText("Never")
    dlg._save()
    spec = dlg.result_body["spec"]
    assert spec["schedule"] == "*/10 * * * *" and "timeZone" not in spec and spec["concurrencyPolicy"] == "Replace"
    assert spec["failedJobsHistoryLimit"] == 5 and spec["suspend"] is True and spec["successfulJobsHistoryLimit"] == 3
    job = spec["jobTemplate"]["spec"]
    assert job["backoffLimit"] == 4 and job["parallelism"] == 2 and job["ttlSecondsAfterFinished"] == 600
    pod = job["template"]["spec"]
    assert pod["containers"][0]["image"] == "reg/job:2" and pod["restartPolicy"] == "Never"


@pytest.mark.parametrize("change, message", [
    (lambda d: d.schedule.setText("every day"), "Schedule"),
    (lambda d: d.time_zone.setText("Mars time"), "Time zone"),
    (lambda d: d.cron_edits["starting_deadline"].setText("x"), "Starting deadline"),
    (lambda d: d.job_edits["parallelism"].setText("-1"), "Parallelism"),
    (lambda d: d.job_edits["ttl"].setText("soon"), "Delete after finished"),
])
def test_validation_blocks_bad_values(qtbot, monkeypatch, change, message):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dlg = make(qtbot, "CronJob", CRON)
    change(dlg)
    assert any(message in p for p in wm.validate("CronJob", dlg.values(), dlg._raw))
    dlg._save()
    assert dlg.result_body is None


# ---- creating ---------------------------------------------------------------------------------------------
def test_new_job_and_cronjob_start_from_a_skeleton_with_the_right_restart_policy(qtbot):
    job = make(qtbot, "Job", wm.skeleton("Job", "shop"), create=True)
    assert not job.running and job.pod.restart.currentText() == "Never" and job.forms[0].image.isEnabled()
    cron = make(qtbot, "CronJob", wm.skeleton("CronJob", "shop"), create=True)
    assert cron.schedule.text() == "0 * * * *" and cron.pod.restart.currentText() == "OnFailure"


def test_a_new_job_is_created_without_a_selector(qtbot):
    dlg = make(qtbot, "Job", wm.skeleton("Job", "shop"), create=True)
    dlg.name_edit.setText("once")
    dlg.job_edits["completions"].setText("5")
    dlg.forms[0].image.setText("busybox:1.36")
    dlg.forms[0].command.setPlainText("echo\nhi")
    dlg._save()
    body = dlg.result_body
    assert body["metadata"] == {"name": "once", "namespace": "shop"} and body["spec"]["completions"] == 5
    assert "selector" not in body["spec"] and "metadata" not in body["spec"]["template"]
    container = body["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "busybox:1.36" and container["command"] == ["echo", "hi"]
    assert body["spec"]["template"]["spec"]["restartPolicy"] == "Never"


def test_a_new_cronjob_is_created_with_its_template_inside_the_job_template(qtbot):
    dlg = make(qtbot, "CronJob", wm.skeleton("CronJob", "shop"), create=True)
    dlg.name_edit.setText("report")
    dlg.schedule.setText("30 6 * * 1")
    dlg.job_edits["backoff"].setText("1")
    dlg._save()
    spec = dlg.result_body["spec"]
    assert spec["schedule"] == "30 6 * * 1" and "concurrencyPolicy" not in spec
    assert spec["jobTemplate"]["spec"]["backoffLimit"] == 1
    assert spec["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["name"] == "app"


def test_cronjob_names_are_limited_to_52_characters(qtbot):
    dlg = make(qtbot, "CronJob", wm.skeleton("CronJob", "shop"), create=True)
    dlg.name_edit.setText("x" * 53)
    assert any("52" in p for p in wm.validate("CronJob", dlg.values(), dlg._check_raw()))
    dlg.name_edit.setText("x" * 52)
    assert not any("52" in p for p in wm.validate("CronJob", dlg.values(), dlg._check_raw()))


def test_the_form_hands_pending_changes_to_yaml(qtbot):
    dlg = make(qtbot, "CronJob", CRON)
    dlg.schedule.setText("0 4 * * *")
    dlg._as_yaml()
    assert dlg.wants_yaml and dlg.pending["spec"]["schedule"] == "0 4 * * *"


# ---- lists and service -----------------------------------------------------------------------------------------
def test_job_lists_have_a_new_button(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    assert ws.views["jobs"][0].new_button is not None and ws.views["cronjobs"][0].new_button is not None


def test_the_service_creates_jobs_and_cronjobs():
    calls = []
    meta = SimpleNamespace(name="x", namespace="ns", uid="u", creation_timestamp=None, labels=None, annotations=None)
    obj = SimpleNamespace(metadata=meta)
    svc = object.__new__(KubeService)
    svc._apis = {"batch": SimpleNamespace(create_namespaced_job=lambda ns, body: calls.append(("job", ns)) or obj,
                                          create_namespaced_cron_job=lambda ns, body: calls.append(("cron", ns)) or obj)}
    for kind in ("Job", "CronJob"):
        try:
            svc.create(kind, {"metadata": {"name": "n", "namespace": "ns"}})
        except AttributeError:
            pass  # the fake object lacks fields the row mapper reads; the API call is what matters
    assert calls == [("job", "ns"), ("cron", "ns")]
