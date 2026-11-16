"""#653 (BUG-01): a malformed optional metrics sample is left out; it does not fail the usage read for the rest of the list."""
from klotse.k8s.client import KubeService


class Usage(KubeService):
    def __init__(self, samples):
        self.samples = samples

    def _metrics(self, *args, **kwargs):
        return self.samples


def test_node_usage_skips_a_malformed_sample_and_keeps_the_others():
    svc = Usage([{"metadata": {"name": "good"}, "usage": {"cpu": "500m", "memory": "1Gi"}},
                 {"metadata": {"name": "bad"}, "usage": {"cpu": "not-a-quantity"}},
                 {"usage": {"cpu": "1"}}])
    usage = svc.node_usage()
    assert list(usage) == ["good"] and usage["good"][0] == 0.5


def test_pod_usage_skips_a_malformed_sample_and_keeps_the_others():
    svc = Usage([{"metadata": {"name": "web", "namespace": "shop"}, "containers": [{"usage": {"cpu": "100m", "memory": "64Mi"}}]},
                 {"metadata": {"name": "broken", "namespace": "shop"}, "containers": [{"usage": {"cpu": "oops"}}]}])
    usage = svc.pod_usage("shop")
    assert list(usage) == [("shop", "web")] and abs(usage[("shop", "web")][0] - 0.1) < 1e-9
