"""#651 (S3): deleting a pod sends the UID the user chose, so the server refuses to delete a replacement that took the name."""
from kubernetes import client

from klotse.k8s.client import KubeService


class Core:
    def __init__(self):
        self.calls = []

    def delete_namespaced_pod(self, name, namespace, **kwargs):
        self.calls.append((name, namespace, kwargs))


def service():
    svc = KubeService.__new__(KubeService)
    svc._core = Core()
    svc.read_only = False
    return svc


def test_a_normal_delete_names_the_uid_as_a_precondition():
    svc = service()
    svc.delete_pod("web-1", "shop", uid="uid-1")
    body = svc._core.calls[0][2]["body"]
    assert isinstance(body, client.V1DeleteOptions)
    assert body.preconditions.uid == "uid-1" and body.grace_period_seconds is None


def test_a_forced_delete_keeps_zero_grace_and_the_precondition():
    svc = service()
    svc.delete_pod("web-1", "shop", True, uid="uid-1")
    body = svc._core.calls[0][2]["body"]
    assert body.grace_period_seconds == 0 and body.propagation_policy == "Background" and body.preconditions.uid == "uid-1"


def test_without_a_uid_the_delete_is_sent_as_before():
    svc = service()
    svc.delete_pod("web-1", "shop")
    assert svc._core.calls[0][2] == {"_request_timeout": 15}  # as before: no body, no precondition
