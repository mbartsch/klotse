"""#525 step 2 (data layer): one Pods read across several namespaces keeps every readable namespace and names the refused ones."""
from types import SimpleNamespace as NS

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ClusterWorkspace


def pod(name, namespace):
    return PodRow(name, namespace, "1/1", "Running", 0, "n", "1d", {"metadata": {"name": name, "namespace": namespace, "uid": "u-" + name}})


class Api(Exception):
    pass


def make_ws(list_pods):
    ws = ClusterWorkspace(NS(list_pods=list_pods), "ctx")
    return ws


def test_rows_of_every_chosen_namespace_are_merged_with_their_namespace(qtbot):
    calls = []

    def list_pods(namespace, **options):
        calls.append(namespace)
        return [pod("web", namespace), pod("api", namespace)]
    ws = make_ws(list_pods)
    qtbot.addWidget(ws)
    result = ws._read_pods_many(["shop", "data"])
    assert calls == ["shop", "data"]
    assert [(r.namespace, r.name) for r in result.rows] == [("shop", "web"), ("shop", "api"), ("data", "web"), ("data", "api")]
    assert result.refused == ()


def test_a_refused_namespace_is_named_and_the_others_still_show(qtbot):
    def list_pods(namespace, **options):
        if namespace == "secret":
            raise Api('(403) Reason: Forbidden: pods is forbidden: User "u" cannot list resource "pods" in namespace "secret"')
        return [pod("web", namespace)]
    ws = make_ws(list_pods)
    qtbot.addWidget(ws)
    result = ws._read_pods_many(["shop", "secret", "data"])
    assert [(r.namespace, r.name) for r in result.rows] == [("shop", "web"), ("data", "web")]
    assert [namespace for namespace, _ in result.refused] == ["secret"]
    assert "Forbidden" in result.refused[0][1]
