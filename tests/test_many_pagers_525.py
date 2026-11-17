"""#525: a huge cluster with several namespaces chosen: one pager per namespace; Load more takes pages from them in turn."""
from types import SimpleNamespace as NS

from klotse.k8s.models import PodRow
from klotse.ui.workspace import ClusterWorkspace, _PagerSet


def pod(name, namespace):
    return PodRow(name, namespace, "1/1", "Running", 0, "n", "1d", {"metadata": {"name": name, "namespace": namespace, "uid": "u-" + name}})


class FakePager:
    """Two pages of one pod each, then done. Mirrors the fields the UI reads."""

    def __init__(self, namespace, pages=2):
        self.namespace, self.pages, self.busy = namespace, pages, False
        self.loaded, self.total, self.taken = 0, None, 0

    @property
    def done(self):
        return self.taken >= self.pages

    def next(self):
        self.taken += 1
        self.loaded += 1
        self.total = self.pages
        return [pod(f"{self.namespace}-{self.taken}", self.namespace)]


def test_pages_come_from_the_namespaces_in_turn_and_the_set_is_done_when_all_are(qtbot):
    a, b = FakePager("shop"), FakePager("data", pages=3)
    group = _PagerSet([a, b])
    order = [group.next()[0].namespace for _ in range(5)]
    assert order == ["shop", "data", "shop", "data", "data"]
    assert group.done and group.loaded == 5 and group.total == 5
    assert group.next() == []


def test_a_huge_cluster_with_several_namespaces_loads_first_pages_then_more_on_request(qtbot):
    made = []

    def pod_pager(namespace, **options):
        pager = FakePager(namespace)
        made.append(pager)
        return pager
    service = NS(pod_pager=pod_pager)
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws.big = ws.huge = True
    result = ws._read_pods_many(("shop", "data"))
    assert [(r.namespace, r.name) for r in result.rows] == [("shop", "shop-1"), ("data", "data-1")]
    assert isinstance(result.pager, _PagerSet) and not result.pager.done
    assert result.refused == ()

    ws._pod_pager = result.pager
    ws.pod_table.set_rows(result.rows)
    ws._run = lambda fn, done, *args, **kw: done(fn(*args))
    ws.load_more_pods()
    assert [r.name for r in ws.pod_table.model.rows] == ["shop-1", "data-1", "shop-2"]
