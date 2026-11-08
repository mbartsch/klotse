from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from klotse.k8s import preflight
from klotse.k8s.client import KubeService
from klotse.ui.preflight_dialog import PreflightDialog
from klotse.ui.workspace import ClusterWorkspace

METRICS = '''# HELP apiserver_requested_deprecated_apis Gauge of deprecated APIs that have been requested
apiserver_requested_deprecated_apis{group="policy",removed_release="1.25",resource="podsecuritypolicies",subresource="",version="v1beta1"} 1
apiserver_requested_deprecated_apis{group="autoscaling",removed_release="1.26",resource="horizontalpodautoscalers",subresource="",version="v2beta2"} 1
apiserver_requested_deprecated_apis{group="",removed_release="",resource="endpoints",subresource="",version="v1"} 1
apiserver_request_total{verb="GET"} 12
'''


def kinds(findings):
    return [(f["severity"], f["kind"], f["name"]) for f in findings]


def test_the_servers_deprecated_request_counts_are_parsed_and_judged_against_the_target():
    requests = preflight.parse_deprecated_metrics(METRICS)
    assert sorted((r["group"], r["version"], r["resource"], r["removed"]) for r in requests) == sorted([
        ("", "v1", "endpoints", ""), ("policy", "v1beta1", "podsecuritypolicies", "1.25"), ("autoscaling", "v2beta2", "horizontalpodautoscalers", "1.26")])
    soon = preflight.deprecated_requested(requests, 26)
    assert ("error", "Deprecated API", "podsecuritypolicies policy/v1beta1") in kinds(soon)
    assert ("error", "Deprecated API", "horizontalpodautoscalers autoscaling/v2beta2") in kinds(soon)
    early = preflight.deprecated_requested(requests, 25)
    assert ("info", "Deprecated API", "horizontalpodautoscalers autoscaling/v2beta2") in kinds(early)  # removed later than the target
    hints = {f["name"]: f["hint"] for f in soon}
    assert "EndpointSlices" in hints["endpoints v1"] and "policy/v1" in hints["podsecuritypolicies policy/v1beta1"]


def test_served_versions_are_the_fallback_when_metrics_cannot_be_read():
    served = [("", "v1"), ("apps", "v1"), ("policy", "v1beta1"), ("flowcontrol.apiserver.k8s.io", "v1beta3"), ("batch", "v1beta1")]
    found = preflight.served_deprecated(served, 29)
    assert ("warn", "Deprecated API", "policy/v1beta1") in kinds(found) and ("warn", "Deprecated API", "batch/v1beta1") in kinds(found)
    assert ("info", "Deprecated API", "flowcontrol.apiserver.k8s.io/v1beta3") in kinds(found)  # removed in 1.32, after the target 1.29
    assert not any("apps" in f["name"] for f in found)
    data = {"served": served, "deprecated": None, "pdbs": [], "nodes": [], "certificates": [], "server_version": "v1.28.2"}
    assert any(f["kind"] == "Deprecated API" for f in preflight.check(data, 29))
    data["deprecated"] = []  # metrics read, nothing deprecated was requested: the served table is not used
    assert not any(f["kind"] == "Deprecated API" for f in preflight.check(data, 29))


def pdb(name, allowed, healthy=2, desired=2, expected=2, ns="shop"):
    return {"metadata": {"name": name, "namespace": ns}, "status": {"disruptionsAllowed": allowed, "currentHealthy": healthy,
                                                                      "desiredHealthy": desired, "expectedPods": expected}}


def test_budgets_that_allow_no_disruption_block_a_drain():
    found = preflight.pdb_findings([pdb("fine", 1), pdb("stuck", 0, healthy=2, desired=2), pdb("broken", 0, healthy=1, desired=2)])
    assert kinds(found) == [("warn", "PodDisruptionBudget", "stuck"), ("error", "PodDisruptionBudget", "broken")]
    assert "no disruption" in found[0]["problem"] and "healthy" in found[1]["hint"]


def node(name, version):
    return {"metadata": {"name": name}, "status": {"nodeInfo": {"kubeletVersion": version}}}


def test_version_skew_now_and_after_the_upgrade():
    nodes = [node("ok", "v1.29.1"), node("edge", "v1.26.0"), node("old", "v1.24.5"), node("ahead", "v1.31.0")]
    found = {f["name"]: f for f in preflight.skew_findings("v1.29.3", nodes, 30)}
    assert "ok" not in found
    assert found["edge"]["severity"] == "error"  # 1.26 would be 4 minors behind 1.30
    assert found["ahead"]["severity"] == "error" and "newer" in found["ahead"]["problem"]
    assert found["old"]["severity"] == "error"
    # 1.24 allowed only two minors behind: already 5 behind 1.29
    assert "already" in found["old"]["problem"]
    after = {f["name"]: f for f in preflight.skew_findings("v1.29.3", [node("n", "v1.26.0")], 30)}
    assert after["n"]["severity"] == "error" and "after the upgrade to 1.30" in after["n"]["problem"]
    exactly = preflight.skew_findings("v1.29.3", [node("n", "v1.27.0")], 30)  # 3 behind 1.30: allowed, but no room left
    assert [f["severity"] for f in exactly] == ["warn"] and "no room" in exactly[0]["hint"]
    assert preflight.skew_findings("v1.29.3", [node("n", "v1.28.0")], 30) == []
    assert preflight.skew_findings("garbage", [node("n", "v1.27.0")], 30)[0]["severity"] == "info"
    assert preflight.skew_findings("v1.29.3", [node("n", "v1.29.0")], 29)[0]["problem"].startswith("is 1.29")


def cert(name, days, ready="True", renew_days=None):
    now = datetime.now(timezone.utc)
    status = {"notAfter": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"), "conditions": [{"type": "Ready", "status": ready}]}
    if renew_days is not None:
        status["renewalTime"] = (now + timedelta(days=renew_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"metadata": {"name": name, "namespace": "shop"}, "status": status}


def test_certificate_dates():
    found = preflight.certificate_findings([cert("old", -3), cert("soon", 10, renew_days=-2), cert("later", 200), cert("notready", 100, ready="False")])
    assert kinds(found) == [("error", "Certificate", "old"), ("warn", "Certificate", "soon"), ("warn", "Certificate", "notready")]
    assert "3 days ago" in found[0]["problem"] and "should have been renewed" in found[1]["hint"]


def test_check_sorts_errors_first_and_picks_the_next_minor():
    data = {"server_version": "v1.29.3", "served": [], "deprecated": [], "pdbs": [pdb("stuck", 0)], "nodes": [node("ahead", "v1.31.0")],
            "certificates": [cert("soon", 5)]}
    found = preflight.check(data, preflight.default_target(data["server_version"]))
    assert [f["severity"] for f in found] == ["error", "warn", "warn"] and preflight.default_target("v1.29.3") == 30
    assert preflight.default_target("garbage") is None and preflight.minor_of("v1.29+") == 29


# ---- the service reads each part on its own ------------------------------------------------------------------------
def preflight_service(metrics=METRICS, fail=()):
    service = KubeService()
    ok = lambda label, value: (_ for _ in ()).throw(PermissionError("no")) if label in fail else value
    service.served_api_versions = lambda: ok("versions", [("", "v1"), ("policy", "v1beta1")])
    service._get_text = lambda path, accept="*/*", timeout=30: ok("metrics", metrics)
    service.list_resource = lambda key, ns=None: ok("pdbs", [SimpleNamespace(raw=pdb("stuck", 0))])
    service.list_nodes = lambda: ok("nodes", [SimpleNamespace(raw=node("n", "v1.29.1"))])
    service._custom = SimpleNamespace(list_cluster_custom_object=lambda *a, **k: {"items": [cert("c", 5)], "metadata": {}})
    from kubernetes import client
    client.VersionApi = lambda api: SimpleNamespace(get_code=lambda **k: SimpleNamespace(git_version="v1.29.3"))
    service._api_client = object()
    return service


def test_the_service_collects_everything_and_names_what_it_could_not_read():
    data, problems = preflight_service().preflight_data()
    assert problems == [] and data["server_version"] == "v1.29.3" and len(data["deprecated"]) == 3 and len(data["pdbs"]) == 1
    assert len(data["nodes"]) == 1 and len(data["certificates"]) == 1 and ("policy", "v1beta1") in data["served"]
    data, problems = preflight_service(fail=("metrics", "pdbs")).preflight_data()
    assert data["deprecated"] is None and data["pdbs"] == []
    assert any("metrics" in p for p in problems) and any("PodDisruptionBudgets" in p for p in problems) and data["nodes"]


# ---- the window --------------------------------------------------------------------------------------------------
def test_the_dialog_suggests_the_next_minor_and_rejudges_when_the_target_changes(qtbot):
    dialog = PreflightDialog("ctx", "cluster-wide")
    qtbot.addWidget(dialog)
    data = {"server_version": "v1.29.3", "served": [], "deprecated": preflight.parse_deprecated_metrics(METRICS), "pdbs": [],
            "nodes": [node("n", "v1.27.0")], "certificates": []}
    dialog.show_data(data, [])
    assert dialog.target.value() == 30 and "API server v1.29.3" in dialog.server_label.text()
    assert "For an upgrade to 1.30" in dialog.summary.text() and dialog.table.rowCount() >= 3
    severities = lambda: [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
    assert any("Error" in text for text in severities())  # removed in 1.25 and 1.26, both before 1.30
    dialog.target.setValue(24)  # earlier than anything removed and no node is too old: only notes
    assert "For an upgrade to 1.24" in dialog.summary.text() and not any("Error" in text for text in severities())
    dialog.target.setValue(30)
    dialog.show_data({**data, "deprecated": [], "nodes": []}, ["pdbs (PermissionError)"])
    assert dialog.table.rowCount() == 0 and dialog.summary.text().startswith("Nothing found") and "pdbs (PermissionError)" in dialog.summary.text()


def test_the_workspace_reads_on_a_worker_and_fills_the_dialog(qtbot):
    data = {"server_version": "v1.29.3", "served": [], "deprecated": [], "pdbs": [pdb("stuck", 0)], "nodes": [], "certificates": []}
    ws = ClusterWorkspace(SimpleNamespace(preflight_data=lambda: (data, [])), "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_preflight()
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1, timeout=5000)
    assert dialog.table.item(0, 1).text() == "PodDisruptionBudget"
    ws.shutdown()
