"""HPAs, Pod Disruption Budgets, quotas, limit ranges, endpoints, priority classes, webhook configurations and leases. The rows
are built from the installed client's own model classes, so a renamed attribute shows up here and not in front of a user."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from kubernetes import client as k

from klotse.k8s import resources as r
from klotse.k8s.client import KubeService
from klotse.ui.navigator import VIEWS

NEW_KEYS = ["horizontalpodautoscalers", "poddisruptionbudgets", "resourcequotas", "limitranges", "priorityclasses",
            "validatingwebhookconfigurations", "mutatingwebhookconfigurations", "endpoints", "endpointslices", "leases"]


def meta(name="x", namespace="shop"):
    return k.V1ObjectMeta(name=name, namespace=namespace, creation_timestamp=datetime.now(timezone.utc) - timedelta(days=2))


def test_hpa_shows_target_current_load_and_replicas():
    h = k.V2HorizontalPodAutoscaler(
        metadata=meta("web"), spec=k.V2HorizontalPodAutoscalerSpec(
            scale_target_ref=k.V2CrossVersionObjectReference(kind="Deployment", name="web", api_version="apps/v1"), min_replicas=2,
            max_replicas=10, metrics=[k.V2MetricSpec(type="Resource", resource=k.V2ResourceMetricSource(
                name="cpu", target=k.V2MetricTarget(type="Utilization", average_utilization=80))),
                k.V2MetricSpec(type="Pods", pods=None)]),
        status=k.V2HorizontalPodAutoscalerStatus(current_replicas=3, desired_replicas=4, current_metrics=[k.V2MetricStatus(
            type="Resource", resource=k.V2ResourceMetricStatus(name="cpu", current=k.V2MetricValueStatus(average_utilization=45)))]))
    row = r.map_hpa(h)
    assert (row.reference, row.min_replicas, row.max_replicas, row.replicas) == ("Deployment/web", 2, 10, "3/4")
    assert row.targets == "cpu 45%/80%, pods metric" and row.age == "2d"


def test_hpa_without_current_metrics_says_unknown_and_defaults_the_minimum():
    h = k.V2HorizontalPodAutoscaler(metadata=meta("w"), spec=k.V2HorizontalPodAutoscalerSpec(
        scale_target_ref=k.V2CrossVersionObjectReference(kind="StatefulSet", name="db"), max_replicas=5,
        metrics=[k.V2MetricSpec(type="Resource", resource=k.V2ResourceMetricSource(
            name="memory", target=k.V2MetricTarget(type="AverageValue", average_value="200Mi")))]),
        status=k.V2HorizontalPodAutoscalerStatus(desired_replicas=0))
    row = r.map_hpa(h)
    assert row.targets == "memory <unknown>/200Mi" and row.min_replicas == 1 and row.replicas == "0/0"


def test_pdb_shows_budget_allowed_disruptions_and_health():
    p = k.V1PodDisruptionBudget(metadata=meta(), spec=k.V1PodDisruptionBudgetSpec(min_available="50%"),
                                status=k.V1PodDisruptionBudgetStatus(current_healthy=3, desired_healthy=2, disruptions_allowed=1, expected_pods=3))
    row = r.map_pdb(p)
    assert (row.min_available, row.max_unavailable, row.allowed, row.healthy) == ("50%", "", 1, "3/2")


def test_quota_lists_used_over_hard_and_cuts_a_long_list():
    q = k.V1ResourceQuota(metadata=meta(), status=k.V1ResourceQuotaStatus(hard={"cpu": "4", "pods": "10"}, used={"pods": "3"}))
    assert r.map_resourcequota(q).usage == "cpu 0/4, pods 3/10"
    many = k.V1ResourceQuota(metadata=meta(), status=k.V1ResourceQuotaStatus(hard={f"k{i}": "1" for i in range(7)}, used={}))
    assert r.map_resourcequota(many).usage.endswith("+3")
    assert r.map_resourcequota(k.V1ResourceQuota(metadata=meta())).usage == ""


def test_limit_range_names_its_types_once():
    lr = k.V1LimitRange(metadata=meta(), spec=k.V1LimitRangeSpec(limits=[k.V1LimitRangeItem(type="Container"), k.V1LimitRangeItem(type="Pod"),
                                                                         k.V1LimitRangeItem(type="Container")]))
    assert r.map_limitrange(lr).limits == "Container, Pod"


def test_endpoints_show_addresses_with_the_first_port_and_a_count():
    e = k.V1Endpoints(metadata=meta(), subsets=[k.V1EndpointSubset(
        addresses=[k.V1EndpointAddress(ip=f"10.0.0.{i}") for i in range(5)], ports=[k.CoreV1EndpointPort(port=80)])])
    row = r.map_endpoints(e)
    assert row.endpoints == "10.0.0.0:80, 10.0.0.1:80, 10.0.0.2:80 +2" and row.count == 5
    assert r.map_endpoints(k.V1Endpoints(metadata=meta())).endpoints == ""


def test_endpoint_slice_counts_ready_endpoints():
    s = k.V1EndpointSlice(metadata=meta(), address_type="IPv4", endpoints=[
        k.V1Endpoint(addresses=["10.0.0.1"], conditions=k.V1EndpointConditions(ready=True)),
        k.V1Endpoint(addresses=["10.0.0.2"], conditions=k.V1EndpointConditions(ready=False)),
        k.V1Endpoint(addresses=["10.0.0.3"])], ports=[k.DiscoveryV1EndpointPort(port=8080, protocol="TCP")])
    row = r.map_endpointslice(s)
    assert (row.address_type, row.ports, row.endpoints) == ("IPv4", "8080/TCP", "2/3")  # no condition counts as ready


def test_priority_class_and_lease():
    p = r.map_priorityclass(k.V1PriorityClass(metadata=meta("high", ""), value=1000000, global_default=True, preemption_policy="Never"))
    assert (p.value, p.default, p.preemption) == (1000000, "yes", "Never")
    lease = k.V1Lease(metadata=meta("node-1", "kube-node-lease"), spec=k.V1LeaseSpec(
        holder_identity="node-1", lease_duration_seconds=40, renew_time=datetime.now(timezone.utc) - timedelta(seconds=9)))
    row = r.map_lease(lease)
    assert (row.holder, row.duration, row.renewed) == ("node-1", "40s", "9s")
    assert r.map_lease(k.V1Lease(metadata=meta(), spec=k.V1LeaseSpec())).holder == ""


def test_webhook_configurations_count_and_name_their_webhooks():
    def hook(name):
        return k.V1ValidatingWebhook(name=name, admission_review_versions=["v1"], side_effects="None",
                                     client_config=k.AdmissionregistrationV1WebhookClientConfig(url="https://example.com"))
    w = k.V1ValidatingWebhookConfiguration(metadata=meta("v", ""), webhooks=[hook(f"h{i}.example.com") for i in range(5)])
    row = r.map_webhooks(w)
    assert row.count == 5 and row.webhooks == "h0.example.com, h1.example.com, h2.example.com +2"
    assert r.map_webhooks(k.V1ValidatingWebhookConfiguration(metadata=meta("v", ""))).count == 0


# ---- the registry, the navigator and the service ------------------------------------------------------------------
def test_every_new_kind_is_registered_with_the_right_scope_and_edit_rules():
    specs = {s.key: s for s in r.SPECS}
    assert all(key in specs for key in NEW_KEYS) and len(specs) == len(r.SPECS)
    assert not [k for k in ("endpoints", "endpointslices", "leases") if specs[k].editable]  # controllers keep these up to date
    assert all(specs[key].editable for key in NEW_KEYS if key not in ("endpoints", "endpointslices", "leases"))
    assert {s.key for s in r.SPECS if s.deletable} >= {"horizontalpodautoscalers", "poddisruptionbudgets", "resourcequotas", "limitranges"}
    assert not any(specs[key].namespaced for key in ("priorityclasses", "validatingwebhookconfigurations", "mutatingwebhookconfigurations"))


def test_every_registered_kind_has_a_navigator_entry():
    keys = set()
    for entry in VIEWS:
        keys |= {leaf[0] for leaf in entry[1]} if len(entry) == 3 else {entry[0]}
    assert {s.key for s in r.SPECS} <= keys


def test_every_spec_names_an_api_the_service_builds():
    apis = {"core", "apps", "batch", "net", "storage", "ext", "rbac", "autoscaling", "policy", "scheduling", "admission", "discovery", "coordination"}
    assert {s.api for s in r.SPECS} <= apis


@pytest.mark.parametrize("key, api, snake, namespaced", [
    ("horizontalpodautoscalers", "autoscaling", "horizontal_pod_autoscaler", True), ("poddisruptionbudgets", "policy", "pod_disruption_budget", True),
    ("priorityclasses", "scheduling", "priority_class", False), ("leases", "coordination", "lease", True)])
def test_the_service_lists_and_watches_through_the_right_api(key, api, snake, namespaced):
    seen = []

    class Api:
        def __getattr__(self, name):
            def call(*a, **kw):
                seen.append(name)
                return SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=None))
            return call

    svc = object.__new__(KubeService)
    svc._apis = {api: Api()}
    svc._core = svc._apps = None  # watch_targets looks them up for the built-in views only
    assert svc.list_resource(key, "shop" if namespaced else None) == []
    assert seen == [f"list_namespaced_{snake}" if namespaced else f"list_{snake}"]
    [(fn, args)] = svc.watch_targets(key, "shop" if namespaced else None)
    assert args == (("shop",) if namespaced else ())


def test_deleting_an_hpa_goes_through_the_autoscaling_api_with_the_uid_precondition():
    calls = []
    svc = object.__new__(KubeService)
    svc.read_only = False
    svc._apis = {"autoscaling": SimpleNamespace(delete_namespaced_horizontal_pod_autoscaler=lambda *a, **kw: calls.append((a, kw)))}
    svc.delete_resource("HorizontalPodAutoscaler", "web", "shop", "uid-1")
    [(args, kw)] = calls
    assert args == ("web", "shop") and kw["body"].preconditions.uid == "uid-1"
    with pytest.raises(ValueError):
        svc.delete_resource("Lease", "x", "kube-node-lease", "u")  # not deletable here
