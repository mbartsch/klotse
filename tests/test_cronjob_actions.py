from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui.workspace import ClusterWorkspace

CRON = {"metadata": {"name": "nightly", "namespace": "ops", "uid": "u1"},
        "spec": {"schedule": "0 2 * * *", "jobTemplate": {"metadata": {"labels": {"app": "x"}},
                                                          "spec": {"template": {"spec": {"containers": [{"name": "c"}]}}}}}}


def test_manual_job_body_follows_kubectl_create_job_from():
    body = KubeService.manual_job_body(CRON, "261107120000")
    assert body["metadata"]["name"] == "nightly-manual-261107120000" and body["metadata"]["namespace"] == "ops"
    assert body["metadata"]["annotations"]["cronjob.kubernetes.io/instantiate"] == "manual"
    assert body["metadata"]["labels"] == {"app": "x"}
    owner = body["metadata"]["ownerReferences"][0]
    assert (owner["kind"], owner["name"], owner["uid"], owner["controller"]) == ("CronJob", "nightly", "u1", True)
    assert body["spec"]["template"]["spec"]["containers"][0]["name"] == "c"


def test_a_long_cronjob_name_is_cut_to_63_characters():
    long = {**CRON, "metadata": {**CRON["metadata"], "name": "a" * 52}}
    name = KubeService.manual_job_body(long, "261107120000")["metadata"]["name"]
    assert len(name) == 63 and name.endswith("-manual-261107120000")


def test_service_creates_the_job_and_patches_suspend():
    seen = {}

    class Batch:
        def read_namespaced_cron_job(self, name, ns):
            return SimpleNamespace(to_dict=lambda: CRON)

        def create_namespaced_job(self, ns, body):
            seen["job"] = (ns, body)

        def patch_namespaced_cron_job(self, name, ns, body):
            seen["patch"] = (name, ns, body)

    service = KubeService.__new__(KubeService)
    service.read_only = False
    service._apis = {"batch": Batch()}
    import klotse.k8s.client as module
    module._raw_backup = module._raw
    try:
        module._raw = lambda o: o.to_dict()
        job = service.trigger_cronjob("nightly", "ops")
    finally:
        module._raw = module._raw_backup
    assert job.startswith("nightly-manual-") and seen["job"][0] == "ops"
    service.set_cronjob_suspended("nightly", "ops", True)
    assert seen["patch"] == ("nightly", "ops", {"spec": {"suspend": True}})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_run_now_and_suspend_ask_first_and_call_the_service(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, trigger_cronjob=lambda *a: calls.append(("run", *a)) or "job-1",
                 set_cronjob_suspended=lambda *a: calls.append(("suspend", *a)))
    row = SimpleNamespace(name="nightly", namespace="ops")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    ws._cronjob_action("cronjob-run", row)
    ws._cronjob_action("cronjob-suspend", row)
    assert calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws._cronjob_action("cronjob-run", row)
    ws._cronjob_action("cronjob-suspend", row)
    ws._cronjob_action("cronjob-resume", row)
    qtbot.waitUntil(lambda: len(calls) == 3)
    assert ("run", "nightly", "ops") in calls and ("suspend", "nightly", "ops", True) in calls
    assert ("suspend", "nightly", "ops", False) in calls
    qtbot.waitUntil(lambda: not ws._workers)


def test_read_only_refuses_them(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, trigger_cronjob=lambda *a: calls.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    ws.set_read_only(True)
    ws._cronjob_action("cronjob-run", SimpleNamespace(name="n", namespace="o"))
    assert calls == []
