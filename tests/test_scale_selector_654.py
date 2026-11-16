"""#654 (BUG-02): the scale monitor lists the pods its workload selects, with matchExpressions as well as matchLabels."""
from types import SimpleNamespace as NS

from klotse.k8s.client import KubeService


class Apps:
    def __init__(self, selector):
        self.selector = selector

    def read_namespaced_deployment(self, name, namespace, **_kw):
        return NS(spec=NS(selector=self.selector, replicas=1), status=NS(replicas=1, ready_replicas=0, available_replicas=0,
                                                                          updated_replicas=0, conditions=[], observed_generation=1, unavailable_replicas=0))


    read_namespaced_replica_set = read_namespaced_stateful_set = read_namespaced_deployment


class Core:
    def __init__(self):
        self.selectors = []

    def list_namespaced_pod(self, namespace, label_selector="", **_kw):
        self.selectors.append(label_selector)
        return NS(items=[])

    def list_namespaced_event(self, namespace, **_kw):
        return NS(items=[])


def run(selector):
    svc = KubeService.__new__(KubeService)
    svc._apps, svc._core = Apps(selector), Core()
    try:  # the rest of the snapshot needs more of the object than this fake has; the pod selector is what is checked
        svc.scale_status("Deployment", "web", "shop")
    except AttributeError:
        pass
    return svc._core.selectors


def test_match_labels_still_select_the_pods():
    sel = NS(match_labels={"app": "web"}, match_expressions=None)
    assert run(sel) == ["app=web"]


def test_an_expression_only_selector_selects_the_pods():
    sel = NS(match_labels=None, match_expressions=[NS(key="tier", operator="In", values=["front", "edge"])])
    assert run(sel) == ["tier in (front,edge)"]
