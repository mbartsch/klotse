"""A namespace load that was abandoned must never install its pod pager (#660, paging race in #494)."""
from types import SimpleNamespace as NS

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ClusterWorkspace


def pod(name, namespace):
    return PodRow(name, namespace, "1/1", "Running", 0, "n", "1d", {"metadata": {"name": name, "namespace": namespace, "uid": "u-" + name}})


def test_an_abandoned_namespace_load_never_installs_its_pager(qtbot):
    current = {"ns": "a"}

    class Pager:
        def __init__(self, namespace):
            self.namespace, self.done, self.busy, self.total, self.loaded = namespace, False, False, 1, 1

        def next(self):
            return [pod("web-" + self.namespace, self.namespace)]

    service = NS(pod_pager=lambda namespace, **options: Pager(namespace))
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    ws.show()
    ws._ready = True
    ws.big = ws.huge = True
    ws.namespace = lambda: current["ns"]
    pending = []
    ws._run = lambda fn, done, *args, **kw: pending.append((fn, done, args))
    ws.load_view = ClusterWorkspace.load_view.__get__(ws)

    ws.load_view("pods")  # namespace a, still running when the user switches
    old_fn, old_done, old_args = pending[0]
    current["ns"] = "b"
    ws.load_view("pods")  # namespace b abandons a's request
    new_fn, new_done, new_args = pending[1]

    new_done(new_fn(*new_args))  # b's first page is accepted
    assert ws._pod_pager.namespace == "b"

    old_done(old_fn(*old_args))  # a finishes late: its rows and pager must be ignored
    assert ws._pod_pager.namespace == "b"
    assert [row.namespace for row in ws.pod_table.model.rows] == ["b"]
