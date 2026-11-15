"""Deleting a Job asks what happens to its pods; the choice reaches the API as the delete's propagation policy."""
from types import SimpleNamespace as NS

from klotse.k8s.client import KubeService
from klotse.ui.workspace import ClusterWorkspace


class FakeBatch:
    def __init__(self):
        self.calls = []

    def delete_namespaced_job(self, name, namespace, body=None, _request_timeout=None):
        self.calls.append((name, namespace, body))


def service_with(batch):
    svc = object.__new__(KubeService)
    svc.read_only = False
    svc._apis = {"batch": batch}
    return svc


def test_the_chosen_propagation_reaches_the_api_and_the_default_is_unchanged():
    batch = FakeBatch()
    svc = service_with(batch)
    svc.delete_resource("Job", "backup", "shop", "uid-1", "Background")
    svc.delete_resource("Job", "backup", "shop", "uid-1", "Orphan")
    svc.delete_resource("Job", "backup", "shop", "uid-1")
    policies = [body.propagation_policy for _, _, body in batch.calls]
    assert policies == ["Background", "Orphan", None]
    assert all(body.preconditions.uid == "uid-1" for _, _, body in batch.calls)


def test_the_question_names_the_three_choices_and_says_pods_stay_by_default():
    text = ClusterWorkspace.job_pods_choice_text("backup", "3")
    assert "Job and its pods" in text and "Job only" in text and "Cancel" in text
    assert "not removed with it unless you say so" in text
    assert "(3)" in text


def test_cancelling_the_job_question_deletes_nothing(qtbot, monkeypatch):
    calls = []
    ws = ClusterWorkspace(NS(delete_resource=lambda *a: calls.append(a)), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(ws, "_ask_job_pods", lambda row, where: None)
    row = NS(name="backup", namespace="shop", raw={"metadata": {"uid": "u"}, "status": {}})
    ws.delete_resource("Job", row)
    assert calls == []
