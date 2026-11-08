from types import SimpleNamespace

from klotse.k8s import pod_security as ps
from klotse.k8s.client import KubeService
from klotse.ui.security_dialog import SecurityDialog
from klotse.ui.workspace import ClusterWorkspace


def pod(name="web-1", ns="shop", owner="web-abc12", owner_kind="ReplicaSet", phase="Running", containers=None, **spec):
    meta = {"name": name, "namespace": ns}
    if owner:
        meta["ownerReferences"] = [{"kind": owner_kind, "name": owner, "controller": True}]
    return {"metadata": meta, "status": {"phase": phase}, "spec": {"containers": containers or [{"name": "c", "securityContext": HARDENED}], **spec}}


HARDENED = {"allowPrivilegeEscalation": False, "runAsNonRoot": True, "runAsUser": 1000, "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"}}


def test_a_hardened_pod_has_no_findings():
    assert ps.baseline_reasons(pod()) == [] and ps.restricted_reasons(pod()) == []
    assert ps.check({"pods": [pod()], "namespaces": []}) == []


def test_baseline_violations():
    bad = pod(hostNetwork=True, hostPID=True, volumes=[{"name": "v", "hostPath": {"path": "/"}}], containers=[
        {"name": "c", "securityContext": {"privileged": True, "capabilities": {"add": ["NET_ADMIN", "CHOWN"]}}, "ports": [{"hostPort": 80}]}])
    reasons = ps.baseline_reasons(bad)
    for part in ("host network", "host PID", "hostPath volume", "privileged container", "added capability NET_ADMIN", "host port"):
        assert part in reasons, part
    assert "CHOWN" not in " ".join(reasons)  # CHOWN is in the baseline set
    assert ps.baseline_reasons(pod(securityContext={"seccompProfile": {"type": "Unconfined"}})) == ["unconfined seccomp or AppArmor"]
    assert ps.baseline_reasons(pod(securityContext={"sysctls": [{"name": "kernel.msgmax", "value": "1"}]})) == ["unsafe sysctl"]
    assert ps.baseline_reasons(pod(securityContext={"seLinuxOptions": {"type": "spc_t"}})) == ["custom SELinux options"]
    assert ps.baseline_reasons(pod(securityContext={"sysctls": [{"name": "net.ipv4.ip_local_port_range", "value": "1 2"}]})) == []
    ephemeral = pod(ephemeralContainers=[{"name": "dbg", "securityContext": {"capabilities": {"add": ["SYS_PTRACE"]}}}])
    assert ps.baseline_reasons(ephemeral) == ["added capability SYS_PTRACE"]  # a kubectl debug container counts


def test_restricted_violations_and_what_counts_as_set_at_pod_level():
    plain = pod(containers=[{"name": "c"}])
    assert ps.baseline_reasons(plain) == []
    assert ps.restricted_reasons(plain) == ["privilege escalation allowed", "may run as root", "no seccomp profile", "does not drop all capabilities"]
    pod_level = pod(containers=[{"name": "c", "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}],
                    securityContext={"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}})
    assert ps.restricted_reasons(pod_level) == []
    root = pod(containers=[{"name": "c", "securityContext": {**HARDENED, "runAsUser": 0}}])
    assert ps.restricted_reasons(root) == ["runs as user 0"]
    extra = pod(containers=[{"name": "c", "securityContext": {**HARDENED, "capabilities": {"drop": ["ALL"], "add": ["NET_BIND_SERVICE", "CHOWN"]}}}])
    assert ps.restricted_reasons(extra) == ["adds capabilities beyond NET_BIND_SERVICE"]
    assert ps.restricted_reasons(pod(volumes=[{"name": "v", "nfs": {"server": "x", "path": "/"}}])) == ["volume type outside the restricted set"]
    assert ps.restricted_reasons(pod(volumes=[{"name": "v", "emptyDir": {}}, {"name": "s", "secret": {"secretName": "x"}}])) == []


def test_findings_are_grouped_by_workload_with_a_count_and_graded():
    privileged = {"name": "c", "securityContext": {**HARDENED, "privileged": True}}
    pods = [pod(f"web-{i}", owner="web-abc12", containers=[privileged]) for i in range(3)]
    pods += [pod("db-1", owner="db-xyz", containers=[{"name": "c"}]), pod("job-1", owner=None, containers=[privileged], phase="Succeeded")]
    found = ps.check({"pods": pods, "namespaces": []})
    assert [(f["severity"], f["kind"], f["name"]) for f in found] == [("error", "Deployment", "web"), ("info", "Deployment", "db")]
    assert "privileged container (3 pods)" in found[0]["problem"] and "restricted" in found[1]["problem"]
    hostpath = pod("log-1", owner="log", owner_kind="DaemonSet", volumes=[{"name": "v", "hostPath": {"path": "/var/log"}}])
    assert ps.check({"pods": [hostpath], "namespaces": []})[0]["severity"] == "error"
    seccomp = pod(securityContext={"seccompProfile": {"type": "Unconfined"}})
    assert ps.check({"pods": [seccomp], "namespaces": []})[0]["severity"] == "warn"  # baseline, but not reaching the node


def test_system_namespaces_get_a_hint_and_static_pods_are_named():
    static = pod("etcd-n1", ns="kube-system", owner="n1", owner_kind="Node", hostNetwork=True)
    found = ps.check({"pods": [static], "namespaces": []})[0]
    assert (found["kind"], found["name"]) == ("Static pods", "on n1") and "System components" in found["hint"]


def test_namespaces_without_enforcement_are_noted_only_when_they_have_pods():
    ns = lambda name, labels=None: {"metadata": {"name": name, "labels": labels or {}}}
    data = {"pods": [pod(ns="a"), pod(ns="b")], "namespaces": [ns("a"), ns("b", {ps.ENFORCE_LABEL: "baseline"}), ns("empty")]}
    notes = [f for f in ps.check(data) if f["kind"] == "Namespace"]
    assert [f["name"] for f in notes] == ["a"]


class Rows:
    def __init__(self, raws):
        self.rows = [SimpleNamespace(raw=r) for r in raws]


def test_the_service_collects_pods_and_namespaces_and_names_what_failed():
    service = KubeService()
    service.list_pods_for_analysis = lambda namespace=None, full=False: Rows([pod()]).rows if full else []
    service.list_namespaces = lambda: Rows([{"metadata": {"name": "shop"}}]).rows
    data, problems = service.pod_security_data("shop")
    assert problems == [] and len(data["pods"]) == 1 and len(data["namespaces"]) == 1
    service.list_namespaces = lambda: (_ for _ in ()).throw(PermissionError("no"))
    data, problems = service.pod_security_data(None)
    assert problems == ["namespaces (PermissionError)"] and len(data["pods"]) == 1


def test_the_dialog_and_workspace(qtbot):
    privileged = {"name": "c", "securityContext": {"privileged": True}}
    data = ({"pods": [pod(containers=[privileged])], "namespaces": []}, [])
    ws = ClusterWorkspace(SimpleNamespace(pod_security_data=lambda namespace=None: data), "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_security()
    assert isinstance(dialog, SecurityDialog) and dialog.windowTitle() == "Pod security"
    qtbot.waitUntil(lambda: dialog.table.rowCount() == 1, timeout=5000)
    assert dialog.table.item(0, 1).text() == "Deployment" and "privileged container" in dialog.table.item(0, 4).text()
    ws.shutdown()


def test_apparmor_annotations():
    key = "container.apparmor.security.beta.kubernetes.io/c"
    for value, bad in (("runtime/default", False), ("localhost/profile", False), ("unconfined", True)):
        p = pod()
        p["metadata"]["annotations"] = {key: value}
        assert (ps.baseline_reasons(p) == ["unconfined seccomp or AppArmor"]) is bad, value
    other = pod()
    other["metadata"]["annotations"] = {"example.com/c": "unconfined"}
    assert ps.baseline_reasons(other) == []
