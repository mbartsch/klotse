from types import SimpleNamespace

from klotse.k8s import health as h
from klotse.k8s.client import KubeService
from klotse.ui.health_dialog import HealthDialog
from klotse.ui.workspace import ClusterWorkspace


def container(name="app", image="nginx:1.27", limits=True, requests=True, probes=True, privileged=False):
    c = {"name": name, "image": image}
    resources = {}
    if limits:
        resources["limits"] = {"memory": "1Gi"}
    if requests:
        resources["requests"] = {"cpu": "100m"}
    c["resources"] = resources
    if probes:
        c["readinessProbe"] = {"httpGet": {"path": "/"}}
        c["livenessProbe"] = {"httpGet": {"path": "/"}}
    if privileged:
        c["securityContext"] = {"privileged": True}
    return c


def deployment(name="api", replicas=2, ready=2, **kw):
    return {"metadata": {"name": name, "namespace": "shop"},
            "spec": {"replicas": replicas, "template": {"spec": {"containers": [container(**kw)]}}},
            "status": {"readyReplicas": ready}}


def problems(findings):
    return [f["problem"] for f in findings]


def test_image_problems():
    assert h.image_problem("nginx") == "has no tag, so it means :latest"
    assert h.image_problem("nginx:latest") == "uses the :latest tag"
    assert h.image_problem("registry:5000/team/app") == "has no tag, so it means :latest"  # the port is not a tag
    assert h.image_problem("registry:5000/team/app:1.2") is None and h.image_problem("app@sha256:abc") is None
    assert h.image_problem("") is None


def test_a_healthy_deployment_has_only_the_notes_it_deserves():
    assert h.workload_findings("Deployment", deployment()) == []
    one = h.workload_findings("Deployment", deployment(replicas=1, ready=1))
    assert [f["severity"] for f in one] == [h.INFO] and "single replica" in one[0]["problem"]


def test_container_risks_are_found_per_container():
    found = h.workload_findings("Deployment", deployment(image="nginx", limits=False, requests=False, probes=False, privileged=True))
    text = " | ".join(problems(found))
    for part in ("no resource limits", "no resource requests", "no readiness probe", "no liveness probe", "has no tag", "runs privileged"):
        assert part in text
    assert {f["severity"] for f in found} == {h.WARN, h.INFO}


def test_replica_state_of_workloads():
    none = h.workload_findings("Deployment", deployment(replicas=3, ready=0))
    assert none[0]["severity"] == h.ERROR and "none of its 3 replicas" in none[0]["problem"]
    some = h.workload_findings("StatefulSet", deployment(replicas=3, ready=1))
    assert some[0]["severity"] == h.WARN and "1 of 3" in some[0]["problem"]
    ds = {"metadata": {"name": "agent", "namespace": "kube-system"}, "spec": {"template": {"spec": {"containers": [container()]}}},
          "status": {"desiredNumberScheduled": 4, "numberReady": 3}}
    assert "3 of 4 pods ready" in h.workload_findings("DaemonSet", ds)[0]["problem"]


def pod(name="web-1", phase="Running", waiting=None, restarts=0, terminated=None, owner=True, conditions=None, spec=None):
    status = {"phase": phase, "containerStatuses": [{"name": "app", "restartCount": restarts,
                                                       "state": {"waiting": {"reason": waiting, "message": "back-off"}} if waiting else {},
                                                       "lastState": {"terminated": {"reason": terminated}} if terminated else {}}],
              "conditions": conditions or []}
    meta = {"name": name, "namespace": "shop"}
    if owner:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "controller": True}]
    return {"metadata": meta, "status": status, "spec": spec or {"containers": [container()]}}


def test_pod_problems():
    crash = h.pod_findings(pod(waiting="CrashLoopBackOff", restarts=9))
    assert [f["problem"] for f in crash] == ["container app is CrashLoopBackOff"]  # not also "restarted 9 times"
    assert crash[0]["severity"] == h.ERROR and crash[0]["hint"] == "back-off"
    assert "OOMKilled" in h.pod_findings(pod(terminated="OOMKilled"))[0]["problem"]
    assert "restarted 7 times" in h.pod_findings(pod(restarts=7))[0]["problem"] and h.pod_findings(pod(restarts=2)) == []
    pending = pod(phase="Pending", conditions=[{"type": "PodScheduled", "status": "False", "reason": "Unschedulable",
                                                 "message": "0/3 nodes are available"}])
    found = h.pod_findings(pending)
    assert found[0]["severity"] == h.ERROR and "cannot be scheduled" in found[0]["problem"] and "0/3" in found[0]["hint"]
    failed = pod(phase="Failed")
    failed["status"]["reason"] = "Evicted"
    assert "failed (Evicted)" in h.pod_findings(failed)[0]["problem"]


def test_nodes_and_claims_and_services():
    node = lambda ready, **extra: {"metadata": {"name": "n1"}, "spec": extra.get("spec", {}),
                                   "status": {"conditions": [{"type": "Ready", "status": ready}] + extra.get("conds", [])}}
    assert h.node_findings(node("True")) == []
    assert h.node_findings(node("False"))[0]["problem"] == "is not Ready"
    assert "MemoryPressure" in h.node_findings(node("True", conds=[{"type": "MemoryPressure", "status": "True"}]))[0]["problem"]
    cordoned = h.node_findings(node("True", spec={"unschedulable": True}))
    assert cordoned[0]["severity"] == h.INFO
    pvc = lambda phase: {"metadata": {"name": "data", "namespace": "shop"}, "status": {"phase": phase}}
    assert h.claim_findings(pvc("Bound")) == [] and "not bound" in h.claim_findings(pvc("Pending"))[0]["problem"]
    svc = {"metadata": {"name": "web", "namespace": "shop"}, "spec": {"selector": {"app": "web"}}}
    ready = pod(conditions=[{"type": "Ready", "status": "True"}])
    ready["metadata"]["labels"] = {"app": "web"}
    assert h.service_findings(svc, [ready]) == []
    assert "no ready pod" in h.service_findings(svc, [])[0]["problem"]
    other_ns = {**ready, "metadata": {**ready["metadata"], "namespace": "elsewhere"}}
    assert h.service_findings(svc, [other_ns])  # a pod in another namespace does not count
    assert h.service_findings({"metadata": {"name": "x", "namespace": "shop"}, "spec": {"type": "ExternalName"}}, []) == []


def test_check_sorts_errors_first_and_judges_bare_pods_but_not_owned_ones():
    data = {"pods": [pod(owner=False, spec={"containers": [container(limits=False)]}), pod("crash", waiting="CrashLoopBackOff")],
            "deployments": [deployment(replicas=1, ready=1)], "nodes": [], "pvcs": []}
    found = h.check(data)
    assert found[0]["severity"] == h.ERROR and found[0]["name"] == "crash"
    assert [f["severity"] for f in found] == sorted((f["severity"] for f in found), key=h.ORDER.get)
    assert any(f["kind"] == "Pod" and "no resource limits" in f["problem"] for f in found)  # the bare pod
    counts, text = h.summary(found)
    assert counts[h.ERROR] == 1 and text.startswith("1 error, ")
    assert h.check({}) == [] and h.summary([])[1] == "0 errors, 0 warnings, 0 notes"


def test_service_reads_every_kind_and_names_what_it_could_not(monkeypatch):
    service = KubeService.__new__(KubeService)
    rows = lambda raw: [SimpleNamespace(raw=raw)]
    service.list_pods = lambda ns: rows({"p": 1})
    service.list_nodes = lambda: (_ for _ in ()).throw(PermissionError("no"))
    service.list_deployments = lambda ns: rows({"d": 1})
    service.list_statefulsets = lambda ns: rows({"s": 1})
    service.list_resource = lambda key, ns: rows({key: 1})
    data, problems_ = service.health_data("shop")
    assert data["pods"] == [{"p": 1}] and data["pvcs"] == [{"persistentvolumeclaims": 1}] and "nodes" not in data
    assert problems_ == ["nodes (PermissionError)"]


def test_dialog_lists_filters_and_goes_to_the_object(qtbot):
    dialog = HealthDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    found = [h.finding(h.ERROR, "Pod", "shop", "crash", "container app is CrashLoopBackOff"),
             h.finding(h.WARN, "Deployment", "shop", "api", "container app has no resource limits"),
             h.finding(h.INFO, "Deployment", "shop", "api", "runs a single replica")]
    dialog.show_findings(found, problems=["nodes (PermissionError)"])
    assert dialog.table.rowCount() == 3 and dialog.table.item(0, 0).text() == "✖ Error"
    assert "1 error, 1 warning, 1 note" in dialog.summary.text() and "Could not read: nodes" in dialog.summary.text()
    dialog.show_notes.setChecked(False)
    assert [dialog.table.isRowHidden(r) for r in range(3)] == [False, False, True]
    dialog.filter.setText("limits")
    assert [dialog.table.isRowHidden(r) for r in range(3)] == [True, False, True]
    got = []
    dialog.go_to_requested.connect(lambda *a: got.append(a))
    dialog._open_row(1, 0)
    assert got == [("Deployment", "shop", "api")]
    dialog.show_error("boom")
    assert "boom" in dialog.summary.text()


def test_workspace_checks_in_a_worker_and_fills_the_dialog(qtbot):
    data = {"deployments": [deployment(replicas=3, ready=0)]}
    service = SimpleNamespace(health_data=lambda ns: (data, []))
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_health()
    qtbot.waitUntil(lambda: dialog.table.rowCount() > 0, timeout=5000)
    assert dialog.table.item(0, 0).text() == "✖ Error" and ws.show_health() is dialog
    ws.shutdown()
    qtbot.waitUntil(lambda: not ws._workers)


def test_a_container_between_restarts_is_reported_before_crashloopbackoff():
    # shape seen on a real cluster: state.terminated reason Error, exit code 1, 3 restarts, no CrashLoopBackOff yet
    failing = pod(restarts=3)
    failing["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Error", "exitCode": 1}}
    found = h.pod_findings(failing)
    assert len(found) == 1 and found[0]["severity"] == h.ERROR
    assert "exited with code 1 (Error)" in found[0]["problem"] and "restarted 3 times" in found[0]["problem"]
    first = pod(restarts=1)
    first["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Error", "exitCode": 2}}
    assert h.pod_findings(first)[0]["severity"] == h.WARN and "1 time" in h.pod_findings(first)[0]["problem"]


def test_a_clean_exit_or_no_restart_is_not_reported():
    done = pod(restarts=2)
    done["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Completed", "exitCode": 0}}
    assert h.pod_findings(done) == []
    never_restarted = pod(restarts=0)
    never_restarted["status"]["containerStatuses"][0]["state"] = {"terminated": {"reason": "Error", "exitCode": 1}}
    assert h.pod_findings(never_restarted) == []
