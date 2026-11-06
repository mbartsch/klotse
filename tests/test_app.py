from datetime import datetime, timedelta, timezone

from kubernetes import client

from klotse.k8s.client import format_age, map_node, map_pod, pod_buckets
from klotse.k8s.resources import Row
from klotse.k8s.models import (DeploymentRow, NamespaceRow, NodeRow, PodRow, ReplicaSetRow,
                                StatefulSetRow)
from klotse.ui import theme as theme_module
from klotse.ui.theme import ACCENT, BORDER
from klotse.ui.main_window import MainWindow

NOW = datetime.now(timezone.utc)


def test_format_age():
    assert format_age(NOW - timedelta(days=3), NOW) == "3d"
    assert format_age(NOW - timedelta(minutes=5), NOW) == "5m"
    assert format_age(NOW - timedelta(seconds=7), NOW) == "7s"


def test_map_pod_waiting_reason_and_ready():
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name="p", namespace="ns", creation_timestamp=NOW),
        spec=client.V1PodSpec(containers=[client.V1Container(name="a"), client.V1Container(name="b")], node_name="n1"),
        status=client.V1PodStatus(phase="Running", container_statuses=[
            client.V1ContainerStatus(name="a", image="i", image_id="x", ready=True, restart_count=2,
                                     state=client.V1ContainerState()),
            client.V1ContainerStatus(name="b", image="i", image_id="x", ready=False, restart_count=1,
                                     state=client.V1ContainerState(waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff"))),
        ]),
    )
    row = map_pod(pod)
    assert (row.ready, row.status, row.restarts, row.node) == ("1/2", "CrashLoopBackOff", 3, "n1")


def test_map_node():
    node = client.V1Node(
        metadata=client.V1ObjectMeta(name="n1", labels={"node-role.kubernetes.io/control-plane": ""}, creation_timestamp=NOW),
        status=client.V1NodeStatus(
            conditions=[client.V1NodeCondition(type="Ready", status="True")],
            addresses=[client.V1NodeAddress(type="InternalIP", address="10.0.0.1")],
            allocatable={"cpu": "4", "memory": "8Gi"},
            node_info=client.V1NodeSystemInfo(kubelet_version="v1.30", architecture="a", boot_id="b", container_runtime_version="c",
                                              kernel_version="k", kube_proxy_version="v", machine_id="m", operating_system="o",
                                              os_image="os", system_uuid="u")),
    )
    row = map_node(node)
    assert (row.status, row.roles, row.ip, row.version) == ("Ready", "control-plane", "10.0.0.1", "v1.30")


class FakeStream:
    def __init__(self):
        import threading
        self.closed = threading.Event()

    def stream(self, _n):
        yield b"hello "
        yield b"world!"
        self.closed.wait(5)  # blocks like a quiet follow=True stream until close()

    # LogStreamer.stop() unblocks the reader via resp.connection.sock.shutdown(); mimic that chain
    @property
    def connection(self):
        return self

    @property
    def sock(self):
        return self

    def shutdown(self, how):
        self.closed.set()

    def close(self):
        self.closed.set()


def open_active(win):
    """What starting the app used to do: connect the kubeconfig's current context. It no longer does that by itself."""
    contexts, active = win.new_service().load_contexts()
    win.open_cluster(active if active in contexts else contexts[0])


class FakeService:
    @staticmethod
    def load_contexts():
        return ["ctx"], "ctx"

    @staticmethod
    def list_secret_names(namespace):
        return []

    @staticmethod
    def list_configmap_names(namespace):
        return []

    def connect(self, ctx):
        pass

    def list_namespaces(self):
        return [NamespaceRow("default", "Active", "1d", "", {"kind": "Namespace"})]

    def list_pods(self, namespace=None):
        return [PodRow("web", "default", "1/1", "Running", 0, "n1", "1d", {"kind": "Pod"})]

    def list_deployments(self, namespace=None):
        return [DeploymentRow("api", "default", "1/1", 1, 1, "1d", {"kind": "Deployment"})]

    def list_replicasets(self, namespace=None):
        return [ReplicaSetRow("api-abc", "default", 1, 1, 1, "Deployment/api", "1d", {"kind": "ReplicaSet"})]

    def list_statefulsets(self, namespace=None):
        return [StatefulSetRow("db", "default", "1/1", "1d", {"kind": "StatefulSet"})]

    def open_log_stream(self, namespace, pod, container=None, tail=500):
        return FakeStream()

    def list_resource(self, key, namespace=None):
        from klotse.k8s.resources import SPEC_BY_KEY

        self.requested.append((key, namespace))
        cols = {attr: "" for _, attr in SPEC_BY_KEY[key].columns if attr not in ("name", "namespace")}
        return [Row("thing", namespace or "default", {"kind": key}, **cols)]

    def overview_cluster(self):
        ev = Row("e1", "default", {"kind": "Event"}, type="Warning", reason="BackOff", object="Pod/web",
                 message="restarting", count=3, last_seen="1m")
        failed = [PodRow(f"broken-{i}", "default", "0/1", "CrashLoopBackOff", 5, "n1", "1d",
                         {"kind": "Pod", "spec": {"containers": [{"name": "app"}]}}) for i in range(2)]
        return {"counts": {"nodes": 1, "nodes_ready": 1, "pods": 1, "running": 1, "pending": 0, "failed": 2,
                           "cpu": "4 cores"}, "events": [ev], "lists": {"pending": [], "failed": failed, "warnings": [ev]}}

    def overview_workloads(self, namespace=None):
        return {"counts": {"pods": 7, "running": 7, "deployments": 3}, "events": []}

    def __init__(self):
        self.requested = []
        self.scale_script = []

    def scale_status(self, kind, name, namespace):
        sc = self.scale_script
        return sc.pop(0) if len(sc) > 1 else sc[0]

    def list_nodes(self):
        return [NodeRow("n1", "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"kind": "Node"})]


def test_window_populates_tables(qtbot):
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    # only the default Overview tab is open; the namespace list always loads for the toolbar filter
    qtbot.waitUntil(lambda: ws.ns_table.model.rowCount() == 1 and ws.overview.events_table.model.rowCount() == 1,
                    timeout=5000)
    assert ws.ns_combo.findText("default") >= 0
    assert [ws.tabs.tabText(i) for i in range(ws.tabs.count())] == ["Overview"]
    assert ws.pod_table.model.rowCount() == 0  # lazy: not opened, not fetched
    for key in ("nodes", "pods", "deployments", "replicasets", "statefulsets"):
        ws.open_view(key)
    ws.open_view("pods")  # re-opening focuses the existing tab instead of duplicating it
    assert ws.tabs.count() == 6
    qtbot.waitUntil(lambda: all(t.model.rowCount() == 1 for t in (ws.node_table, ws.pod_table, ws.deploy_table,
                                                                  ws.rs_table, ws.sts_table)), timeout=5000)
    ws.pod_table.view.selectRow(0)
    assert "kind: Pod" in ws.detail.toPlainText()


def test_edit_flow_calls_replace(qtbot, monkeypatch):
    from klotse.ui import workspace as mw

    calls = []
    svc = FakeService()
    svc.replace = lambda kind, body: calls.append((kind, body))

    class FakeDialog:
        Accepted = 1
        result_body = {"metadata": {"name": "web", "namespace": "default"}}

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return 1

    class FakeForm(FakeDialog):  # a Pod has a form now; this one accepts at once
        wants_yaml = False

        def deleteLater(self):
            pass

    monkeypatch.setattr(mw, "EditDialog", FakeDialog)
    monkeypatch.setattr(mw, "WorkloadEditDialog", FakeForm)
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("pods")
    qtbot.waitUntil(lambda: ws.pod_table.model.rowCount() == 1, timeout=5000)
    ws.pod_table.edit_requested.emit("Pod", ws.pod_table.model.rows[0])
    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    assert calls[0][0] == "Pod"


def test_parse_pairs_and_build_namespace():
    from klotse.ui.namespace_dialog import build_namespace, parse_pairs

    assert parse_pairs("a=1\n\n b = x=y \n") == {"a": "1", "b": "x=y"}
    for bad in ("novalue", "=v"):
        try:
            parse_pairs(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    body = build_namespace("n", {"a": "1"}, {}, ["x/y"])
    assert body["metadata"] == {"name": "n", "labels": {"a": "1"}, "finalizers": ["x/y"]}


def test_namespace_dialog_validates_name(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from klotse.ui.namespace_dialog import NamespaceDialog

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    dlg = NamespaceDialog()
    qtbot.addWidget(dlg)
    dlg.name.setText("Bad_Name")
    dlg._create()
    assert dlg.result_body is None
    dlg.name.setText("good-ns")
    dlg.labels.setPlainText("team=x")
    dlg._create()
    assert dlg.result_body["metadata"]["labels"] == {"team": "x"}


def test_scale_flow_calls_service(qtbot, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    calls = []
    svc = FakeService()
    svc.scale = lambda *a: calls.append(a)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("3", True))
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("deployments")
    qtbot.waitUntil(lambda: ws.deploy_table.model.rowCount() == 1, timeout=5000)
    ws.deploy_table.scale_requested.emit("Deployment", ws.deploy_table.model.rows[0])
    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    assert calls[0] == ("Deployment", "api", "default", 3)


def test_kubectl_args():
    from klotse.ui.terminal import kubectl_args

    assert kubectl_args("shell", "c", "ns", "p") == ["kubectl", "--context", "c", "-n", "ns", "exec", "-it", "p", "--", "sh"]
    assert kubectl_args("attach", "c", "ns", "p", "app")[-3:] == ["attach", "-it", "p", "-c", "app"][-3:]
    assert kubectl_args("attach", "c", "ns", "p", "app")[5:] == ["attach", "-it", "p", "-c", "app"]


def test_pod_terminate_and_connect(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from klotse.ui import workspace as mw

    deleted, opened = [], []
    svc = FakeService()
    svc.delete_pod = lambda *a: deleted.append(a)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(mw, "open_terminal", lambda argv, terminal: opened.append(argv))
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("pods")
    qtbot.waitUntil(lambda: ws.pod_table.model.rowCount() == 1, timeout=5000)
    row = ws.pod_table.model.rows[0]
    ws.pod_table.action_requested.emit("terminate", row)
    qtbot.waitUntil(lambda: bool(deleted), timeout=5000)
    assert deleted[0] == ("web", "default")
    ws.pod_table.action_requested.emit("shell", row)
    qtbot.waitUntil(lambda: bool(opened))
    assert opened[0][:6] == ["kubectl", "--context", "ctx", "-n", "default", "exec"]


def test_navigator_and_logs(qtbot):
    from klotse.ui.navigator import Navigator

    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: win.nav.cluster_item("ctx").childCount() > 0, timeout=5000)
    top = win.nav.cluster_item("ctx")
    labels = [top.child(i).text(0) for i in range(top.childCount())]
    assert labels == ["Overview", "Nodes", "Workloads", "Config", "Network", "Storage", "Access Control", "Namespaces", "Events",
                      "Helm", "Custom Resources"]
    groups = {top.child(i).text(0): [top.child(i).child(j).text(0) for j in range(top.child(i).childCount())]
              for i in range(top.childCount()) if top.child(i).childCount()}
    assert groups["Workloads"] == ["Overview", "Pods", "Deployments", "DaemonSets", "StatefulSets",
                                   "ReplicaSets", "Jobs", "CronJobs", "Horizontal Pod Autoscalers"]
    assert groups["Config"][:2] == ["ConfigMaps", "Secrets"] and "Resource Quotas" in groups["Config"]
    assert groups["Storage"][0] == "Persistent Volume Claims"
    assert groups["Network"] == ["Services", "Endpoints", "Endpoint Slices", "Ingresses", "Network Policies"]
    assert win.windowTitle().endswith("ctx")
    win.nav.view_requested.emit("ctx", "deployments")
    assert ws.tabs.tabText(ws.tabs.currentIndex()) == "Deployments"

    ws.open_view("pods")
    qtbot.waitUntil(lambda: ws.pod_table.model.rowCount() == 1, timeout=5000)
    row = ws.pod_table.model.rows[0]
    row.raw = {"spec": {"containers": [{"name": "main"}]}}
    ws.pod_action("logs", row)
    assert ws.log_dock.isVisibleTo(win) and ws.log_tabs.count() == 1
    view = ws.log_tabs.widget(0)
    qtbot.waitUntil(lambda: "hello world" in view.text.toPlainText(), timeout=5000)
    ws.pod_action("logs", row)  # same pod: focuses, no duplicate tab
    assert ws.log_tabs.count() == 1
    streamer = view._streamer
    ws._close_log_tab(0)
    qtbot.waitUntil(streamer.isFinished, timeout=5000)
    assert not ws.log_dock.isVisibleTo(win)


def test_overview_cards_and_events(qtbot):
    svc = FakeService()
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws.overview.cards["failed"].value.text() == "2", timeout=5000)
    assert ws.overview.cards["cpu"].value.text() == "4 cores"
    assert ws.overview.cards["warnings"].value.text() == "–"  # key absent from counts
    assert theme_module.BAD in ws.overview.cards["failed"].value.styleSheet()  # non-zero failures are flagged
    ws.overview.events_table.view.selectRow(0)
    assert "kind: Event" in ws.detail.toPlainText()
    ws.open_view("workloads")
    qtbot.waitUntil(lambda: ws.wl_overview.cards["pods"].value.text() == "7", timeout=5000)


def test_generic_views_lazy_and_namespace_scoped(qtbot):
    from klotse.k8s.resources import SPECS

    svc = FakeService()
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws.ns_table.model.rowCount() == 1, timeout=5000)
    assert svc.requested == []  # nothing fetched until a tab is opened
    for spec in SPECS:
        assert spec.key in ws.views and spec.key in ws.loaders
    ws.open_view("services")
    qtbot.waitUntil(lambda: ("services", "default") in svc.requested, timeout=5000)
    ws.open_view("persistentvolumes")
    qtbot.waitUntil(lambda: ("persistentvolumes", None) in svc.requested, timeout=5000)  # its own initial load
    ws.set_namespace("default")  # present once the namespace list has loaded
    assert ws.ns_combo.currentText() == "default"
    svc.requested.clear()
    ws.load_scoped()  # foreground is cluster-scoped (PVs): a namespace change reloads nothing
    qtbot.wait(100)
    assert svc.requested == []
    ws.open_view("services")  # a background tab reloads with the new namespace when it comes to the front
    qtbot.waitUntil(lambda: ("services", "default") in svc.requested, timeout=5000)
    ws.tabs.setCurrentWidget(ws.views["persistentvolumes"][0])  # switching by clicking a tab reloads it too
    qtbot.waitUntil(lambda: ("persistentvolumes", None) in svc.requested, timeout=5000)
    # read-only kinds expose no Edit action
    assert ws.views["secrets"][0].editable and not ws.views["events"][0].editable  # secrets: form editor only
    assert ws.views["services"][0].editable


def test_secret_mapper_masks_values():
    from klotse.k8s.resources import map_secret

    s = client.V1Secret(
        metadata=client.V1ObjectMeta(name="s", namespace="ns", creation_timestamp=NOW, annotations={
            "kubectl.kubernetes.io/last-applied-configuration": '{"data":{"pw":"c2VjcmV0"}}', "keep": "me"}),
        type="Opaque", data={"pw": "c2VjcmV0", "user": "YWRtaW4="})
    row = map_secret(s)
    assert row.keys == 2 and row.type == "Opaque"
    assert row.raw["data"] == {"pw": "<hidden>", "user": "<hidden>"}
    ann = row.raw["metadata"]["annotations"]
    assert ann["kubectl.kubernetes.io/last-applied-configuration"] == "<hidden>" and ann["keep"] == "me"
    assert "c2VjcmV0" not in str(row.raw)


def test_pod_buckets():
    from klotse.k8s.models import PodRow

    def pod(status):
        return PodRow("p", "n", "1/1", status, 0, "", "1d")

    pods = [pod(s) for s in ("Running", "Running", "Pending", "Init:0/1", "CrashLoopBackOff", "Completed", "Terminating")]
    assert pod_buckets(pods) == (2, 3, 1)


def test_job_cronjob_and_pvc_mappers():
    from klotse.k8s.resources import map_cronjob, map_job, map_pvc

    meta = client.V1ObjectMeta(name="j", namespace="ns", creation_timestamp=NOW)
    job = client.V1Job(metadata=meta, spec=client.V1JobSpec(completions=3, template=client.V1PodTemplateSpec()),
                       status=client.V1JobStatus(succeeded=1, active=1, start_time=NOW - timedelta(minutes=5)))
    row = map_job(job)
    assert (row.completions, row.status, row.duration) == ("1/3", "Running", "5m")
    done = client.V1Job(metadata=meta, spec=client.V1JobSpec(template=client.V1PodTemplateSpec()),
                        status=client.V1JobStatus(succeeded=1, conditions=[client.V1JobCondition(type="Complete", status="True")]))
    assert map_job(done).status == "Complete" and map_job(done).completions == "1/-"
    cj = client.V1CronJob(metadata=meta, spec=client.V1CronJobSpec(schedule="*/5 * * * *", suspend=True,
                          job_template=client.V1JobTemplateSpec()), status=client.V1CronJobStatus())
    c = map_cronjob(cj)
    assert (c.schedule, c.suspend, c.active, c.last_schedule) == ("*/5 * * * *", "True", 0, "never")
    pvc = client.V1PersistentVolumeClaim(metadata=meta, spec=client.V1PersistentVolumeClaimSpec(
        access_modes=["ReadWriteOnce"], storage_class_name="hostpath", volume_name="pv1"),
        status=client.V1PersistentVolumeClaimStatus(phase="Bound", capacity={"storage": "1Gi"}))
    p = map_pvc(pvc)
    assert (p.status, p.capacity, p.access_modes, p.storage_class) == ("Bound", "1Gi", "ReadWriteOnce", "hostpath")


class MultiService(FakeService):
    """One instance per cluster, like the real factory; remembers which context it connected to."""

    instances = []

    @staticmethod
    def load_contexts():
        return ["a", "b", "bad"], "a"

    def __init__(self):
        super().__init__()
        self.context = None
        MultiService.instances.append(self)

    def connect(self, ctx):
        if ctx == "bad":
            raise RuntimeError("unreachable")
        self.context = ctx

    def list_pods(self, namespace=None):
        return [PodRow(f"pod-of-{self.context}", "default", "1/1", "Running", 0, "n1", "1d", {"kind": "Pod"})]


def test_multiple_clusters_open_in_separate_tabs(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    MultiService.instances.clear()
    win = MainWindow(MultiService)
    qtbot.addWidget(win)
    open_active(win)
    assert [win.clusters.tabText(i) for i in range(win.clusters.count())] == ["a"]  # active context opens at startup
    qtbot.waitUntil(lambda: win.nav.cluster_item("a").childCount() > 0, timeout=5000)

    win.nav.context_clicked.emit("b")
    assert [win.clusters.tabText(i) for i in range(win.clusters.count())] == ["a", "b"]
    assert win.windowTitle().endswith("b") and win.workspace() is win.workspace("b")
    qtbot.waitUntil(lambda: win.nav.cluster_item("b").childCount() > 0, timeout=5000)
    assert win.nav.cluster_item("a").childCount() > 0  # both clusters stay expanded in the navigator

    win.nav.context_clicked.emit("a")  # already open: focus, no duplicate tab
    assert win.clusters.count() == 2 and win.workspace() is win.workspace("a")

    # each workspace has its own connection and its own open views
    a, b = win.workspace("a"), win.workspace("b")
    assert a.service is not b.service
    qtbot.waitUntil(lambda: a.service.context == "a" and b.service.context == "b", timeout=5000)
    win.nav.view_requested.emit("b", "pods")
    assert win.workspace() is b and "pods" in b.open_keys() and "pods" not in a.open_keys()
    qtbot.waitUntil(lambda: b.pod_table.model.rowCount() == 1, timeout=5000)
    assert b.pod_table.model.rows[0].name == "pod-of-b"
    a.open_view("pods")
    qtbot.waitUntil(lambda: a.pod_table.model.rowCount() == 1, timeout=5000)
    assert a.pod_table.model.rows[0].name == "pod-of-a"  # no cross-talk between clusters

    # closing a cluster tab tears down only that cluster
    win._close_cluster_tab(win.clusters.indexOf(b))
    assert list(win.workspaces) == ["a"] and win.nav.cluster_item("b").childCount() == 0
    assert win.nav.cluster_item("a").childCount() > 0 and win.clusters.count() == 1
    win.nav.context_clicked.emit("b")  # and it can be reopened
    assert win.clusters.count() == 2


def test_failed_cluster_connection_closes_its_tab(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    win = MainWindow(MultiService)
    qtbot.addWidget(win)
    open_active(win)
    win.nav.context_clicked.emit("bad")
    qtbot.waitUntil(lambda: bool(warnings), timeout=5000)
    assert "unreachable" in warnings[0] and "bad" in warnings[0]
    assert "bad" not in win.workspaces and win.clusters.count() == 1  # dead tab removed, "a" untouched
    assert win.nav.cluster_item("bad").childCount() == 0


def _pod(name, status="Running", ready="1/1", issue="", deleting=False):
    return {"name": name, "status": status, "ready": ready, "restarts": 0, "issue": issue, "deleting": deleting}


def _st(ready, pods, observed=True, deadline=False, warnings=()):
    return {"desired": 0, "ready": ready, "available": ready, "pods": pods, "warnings": list(warnings),
            "observed": observed, "deadline_exceeded": deadline}


def test_evaluate_scale_states():
    from klotse.ui.scale_monitor import evaluate

    ok = [_pod("a"), _pod("b"), _pod("c")]
    assert evaluate(3, _st(3, ok), 12, 300) == ("complete", "Complete: 3/3 ready in 12s")
    state, text = evaluate(3, _st(1, [_pod("a"), _pod("b", "ContainerCreating", "0/1"), _pod("c", "Pending", "0/1")]), 5, 300)
    assert state == "progress" and "1/3 ready" in text and "2 starting" in text
    bad = [_pod("a"), _pod("b", "ImagePullBackOff", "0/1", "ImagePullBackOff"), _pod("c", "ContainerCreating", "0/1")]
    state, text = evaluate(3, _st(1, bad), 5, 300)
    assert state == "progress" and "1 failing" in text and "1 starting" in text
    assert evaluate(3, _st(1, bad), 301, 300)[0] == "timeout" and "1 failing" in evaluate(3, _st(1, bad), 301, 300)[1]
    assert evaluate(3, _st(1, bad, deadline=True), 5, 300)[0] == "failed"
    assert evaluate(3, _st(3, ok, observed=False), 1, 300)[0] == "progress"  # stale status before the controller reacts
    # scale down: extra live pods must be gone first; terminating ones are reported but do not block completion
    down = [_pod("a"), _pod("b"), _pod("c")]
    state, text = evaluate(1, _st(3, down), 2, 300)
    assert state == "progress" and "2 to remove" in text
    done = [_pod("a"), _pod("b", deleting=True)]
    state, text = evaluate(1, _st(1, done), 3, 300)
    assert state == "progress" and "1 terminating" in text  # not complete until the pod is really gone
    assert evaluate(1, _st(1, [_pod("a")]), 4, 300)[0] == "complete"
    assert evaluate(0, _st(0, []), 4, 300)[0] == "complete"


def _sync_run(fn, on_result, *args, on_error=None):
    try:
        res = fn(*args)
    except Exception as e:  # mirror Worker: errors go to the error callback
        on_error(f"{type(e).__name__}: {e}")
    else:
        on_result(res)


def test_scale_monitor_progress_failure_and_completion(qtbot):
    from klotse.ui.scale_monitor import ScaleMonitor

    svc = FakeService()
    bad = _pod("b", "ErrImagePull", "0/1", "ErrImagePull")
    svc.scale_script = [
        _st(1, [_pod("a"), bad], warnings=["b: Failed: pull access denied"]),
        _st(2, [_pod("a"), _pod("b")]),
    ]
    mon = ScaleMonitor(_sync_run, svc, "Deployment", "api", "default", 1, 2, poll_ms=10, timeout_s=60)
    qtbot.addWidget(mon)
    assert mon.pods.rowCount() == 2 and "failing" in mon.status.text()  # first poll ran synchronously
    assert mon.pods.item(1, 4).text() == "ErrImagePull" and "pull access denied" in mon.events.toPlainText()
    assert mon.bar.value() == 1 and mon.bar.maximum() == 2
    done = []
    mon.finished.connect(lambda state, text: done.append((state, text)))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert done[0][0] == "complete" and mon.bar.value() == 2 and mon.state == "complete"
    assert mon.events.toPlainText() == ""  # warnings clear once the pods recover


def test_scale_monitor_timeout_and_poll_errors(qtbot):
    from klotse.ui.scale_monitor import ScaleMonitor

    svc = FakeService()
    svc.scale_status = lambda *a: (_ for _ in ()).throw(RuntimeError("api down"))
    mon = ScaleMonitor(_sync_run, svc, "Deployment", "api", "default", 1, 2, poll_ms=10, timeout_s=0.15)
    qtbot.addWidget(mon)
    assert "retrying" in mon.status.text()
    qtbot.waitUntil(lambda: mon.state == "timeout", timeout=3000)
    assert "api down" in mon.status.text()
    mon.stop()


def test_scale_monitor_restart_ignores_stale_results(qtbot):
    from klotse.ui.scale_monitor import ScaleMonitor

    svc = FakeService()
    svc.scale_script = [_st(0, [])]
    pending = []
    mon = ScaleMonitor(lambda fn, ok, *a, on_error=None: pending.append((fn, ok)), svc, "Deployment", "api", "default", 0, 3,
                       poll_ms=10, timeout_s=60)
    qtbot.addWidget(mon)
    stale_fn, stale_ok = pending.pop()  # a poll for the first scale is in flight...
    mon.restart(3, 0)  # ...then the user scales again
    stale_ok(stale_fn())  # the old result arrives late and must be ignored
    assert mon.state == "progress" and "Scaling Deployment api: 3 → 0" in mon.header.text()
    assert mon.pods.rowCount() == 0 and mon.status.text() == "Starting…"


def test_workspace_scale_opens_monitor_and_tracks_to_completion(qtbot, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from klotse.ui import scale_monitor as sm

    monkeypatch.setattr(sm, "POLL_MS", 20)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("2", True))
    svc = FakeService()
    scaled = []
    svc.scale = lambda *a: scaled.append(a)
    svc.scale_script = [_st(0, [_pod("p1", "Pending", "0/1")]), _st(2, [_pod("p1"), _pod("p2")])]
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("deployments")
    qtbot.waitUntil(lambda: ws.deploy_table.model.rowCount() == 1, timeout=5000)
    ws.scale_resource("Deployment", ws.deploy_table.model.rows[0])
    qtbot.waitUntil(lambda: ws.log_tabs.count() == 1, timeout=5000)
    mon = ws.log_tabs.widget(0)
    assert ws.log_tabs.tabText(0) == "Scale: api" and ws.log_dock.isVisibleTo(ws)
    assert scaled[0] == ("Deployment", "api", "default", 2)
    qtbot.waitUntil(lambda: mon.state == "complete", timeout=5000)
    assert "Complete" in win.status.currentMessage() or mon.status.text().startswith("Complete")
    # scaling the same workload again reuses the tab instead of adding another
    gen_before = mon._gen
    ws.scale_resource("Deployment", ws.deploy_table.model.rows[0])
    qtbot.waitUntil(lambda: mon._gen > gen_before, timeout=5000)  # restarted in place
    qtbot.waitUntil(lambda: mon.state == "complete", timeout=5000)
    assert ws.log_tabs.count() == 1
    ws._close_log_tab(0)
    assert not ws.log_dock.isVisibleTo(ws)


def test_scale_down_monitor_counts_pods_actually_removed(qtbot):
    from klotse.ui.scale_monitor import ScaleMonitor

    svc = FakeService()
    three = [_pod("a"), _pod("b"), _pod("c")]
    svc.scale_script = [
        _st(1, [_pod("a"), _pod("b", deleting=True), _pod("c", deleting=True)]),  # marked terminating, still present
        _st(1, [_pod("a"), _pod("c", deleting=True)]),  # one gone
        _st(1, [_pod("a")]),  # all gone
    ]
    mon = ScaleMonitor(_sync_run, svc, "Deployment", "api", "default", 3, 1, poll_ms=10, timeout_s=60)
    qtbot.addWidget(mon)
    assert mon.bar.maximum() == 2 and mon.bar.format() == "%v / %m removed"
    assert mon.state == "progress" and mon.bar.value() == 0 and "2 terminating" in mon.status.text()
    qtbot.waitUntil(lambda: mon.state == "complete", timeout=3000)
    assert mon.bar.value() == 2 and mon.pods.rowCount() == 1


def test_map_node_formats_cpu_cores_and_memory_gib():
    node = client.V1Node(
        metadata=client.V1ObjectMeta(name="n", creation_timestamp=NOW),
        status=client.V1NodeStatus(allocatable={"cpu": "1500m", "memory": "16261800Ki"}))
    row = map_node(node)
    assert row.cpu == 1.5 and abs(row.memory - 15.509) < 0.001  # 16261800 KiB / 2**20
    whole = map_node(client.V1Node(metadata=client.V1ObjectMeta(name="n", creation_timestamp=NOW),
                                   status=client.V1NodeStatus(allocatable={"cpu": "20", "memory": "8Gi"})))
    assert (whole.cpu, whole.memory) == (20.0, 8.0)
    bare = map_node(client.V1Node(metadata=client.V1ObjectMeta(name="n", creation_timestamp=NOW), status=client.V1NodeStatus()))
    assert (bare.cpu, bare.memory) == (0.0, 0.0)  # no allocatable reported


def test_nodes_table_displays_formatted_but_sorts_numerically(qtbot):
    from PySide6.QtCore import Qt

    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    ws.open_view("nodes")
    qtbot.waitUntil(lambda: ws.node_table.model.rowCount() == 1, timeout=5000)
    m = ws.node_table.model
    cols = [c[0] for c in m.columns]
    assert "CPU (cores)" in cols and "Memory (GiB)" in cols
    cpu, mem = m.index(0, cols.index("CPU (cores)")), m.index(0, cols.index("Memory (GiB)"))
    assert m.data(cpu) == "4.0" and m.data(mem) == "8.00"
    assert m.data(cpu, Qt.EditRole) == 4.0 and isinstance(m.data(mem, Qt.EditRole), float)  # sort keys stay numeric


def test_navigator_sections_and_state_moves(qtbot):
    from klotse.ui.navigator import Navigator

    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a", "b", "c"])

    def sections():
        return {nav.topLevelItem(i).text(0).split(" (")[0]: [nav.topLevelItem(i).child(j).text(0)
                                                             for j in range(nav.topLevelItem(i).childCount())]
                for i in range(nav.topLevelItemCount())}

    assert sections() == {"CONNECTED": [], "DISCONNECTED": ["a", "b", "c"]}
    assert nav.topLevelItem(0).text(0) == "CONNECTED (0)" and nav.topLevelItem(1).text(0) == "DISCONNECTED (3)"
    nav.set_state("c", "connecting")  # connecting counts as connected so it jumps to the top at once
    nav.set_state("a", "connected")
    assert sections() == {"CONNECTED": ["a", "c"], "DISCONNECTED": ["b"]}  # kubeconfig order kept inside a section
    assert nav.topLevelItem(0).text(0) == "CONNECTED (2)" and nav.topLevelItem(1).text(0) == "DISCONNECTED (1)"
    nav.show_views("a")
    assert nav.cluster_item("a").childCount() > 0 and nav.cluster_item("a").parent() is nav.topLevelItem(0)
    nav.hide_views("a")
    nav.set_state("a", "failed")  # a failed connection goes back to Disconnected, with its red dot
    assert sections() == {"CONNECTED": ["c"], "DISCONNECTED": ["a", "b"]} and nav.state("a") == "failed"
    nav.set_state("c", "idle")
    assert sections() == {"CONNECTED": [], "DISCONNECTED": ["a", "b", "c"]}


def test_navigator_headers_are_inert_and_menu_actions(qtbot):
    from PySide6.QtCore import Qt

    from klotse.ui.navigator import Navigator

    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a"])
    clicks = []
    nav.context_clicked.connect(clicks.append)
    nav.view_requested.connect(lambda *x: clicks.append(x))
    nav._on_click(nav.topLevelItem(0), 0)  # clicking a section header does nothing
    assert clicks == [] and not (nav.topLevelItem(0).flags() & Qt.ItemIsSelectable)
    assert [label for label, _ in nav.actions_for("a")] == ["Connect"]
    nav.set_state("a", "failed")
    assert [label for label, _ in nav.actions_for("a")] == ["Connect"]  # retry
    nav.set_state("a", "connecting")
    assert [label for label, _ in nav.actions_for("a")] == ["Reconnect", "Disconnect"]  # can cancel while connecting
    nav.set_state("a", "connected")
    nav.show_views("a")
    seen = []
    nav.disconnect_requested.connect(seen.append)
    dict(nav.actions_for("a"))["Disconnect"].emit("a")
    assert seen == ["a"]
    group = nav.cluster_item("a").child(2)  # right-clicking anywhere under a cluster resolves to that cluster
    assert nav._cluster_of(group.child(0)) is nav.cluster_item("a") and nav._cluster_of(nav.topLevelItem(0)) is None
    nav._on_click(group.child(0), 0)
    assert clicks == [("a", "workloads")]


def test_disconnect_reconnect_via_navigator(qtbot):
    MultiService.instances.clear()
    win = MainWindow(MultiService)
    qtbot.addWidget(win)
    open_active(win)
    qtbot.waitUntil(lambda: win.nav.cluster_item("a").childCount() > 0, timeout=5000)
    win.nav.context_clicked.emit("b")
    qtbot.waitUntil(lambda: win.nav.cluster_item("b").childCount() > 0, timeout=5000)
    assert win.nav.cluster_item("a").parent() is win.nav.topLevelItem(0) and win.nav.state("bad") == "idle"
    win.nav.disconnect_requested.emit("b")  # what the right-click menu emits
    assert list(win.workspaces) == ["a"] and win.nav.state("b") == "idle"
    assert win.nav.cluster_item("b").parent() is win.nav.topLevelItem(1) and win.nav.cluster_item("b").childCount() == 0
    old = win.workspace("a")
    win.nav.reconnect_requested.emit("a")
    assert win.workspace("a") is not old and list(win.workspaces) == ["a"]
    qtbot.waitUntil(lambda: win.nav.state("a") == "connected" and win.nav.cluster_item("a").childCount() > 0, timeout=5000)
    win.nav.connect_requested.emit("b")  # menu "Connect" on a disconnected cluster
    assert sorted(win.workspaces) == ["a", "b"]


def test_disconnect_while_connecting_drops_late_result(qtbot):
    import threading

    gate = threading.Event()

    class SlowService(MultiService):
        def connect(self, ctx):
            gate.wait(5)  # the connection is "in flight" until released
            super().connect(ctx)

    win = MainWindow(SlowService)
    qtbot.addWidget(win)
    open_active(win)
    assert win.nav.state("a") == "connecting" and win.nav.cluster_item("a").parent() is win.nav.topLevelItem(0)
    win.nav.disconnect_requested.emit("a")  # cancel while still connecting
    assert win.workspaces == {} and win.nav.state("a") == "idle"
    gate.set()
    qtbot.wait(300)  # the late connect result must be ignored: no resurrected state, no errors
    assert win.nav.state("a") == "idle" and win.nav.cluster_item("a").childCount() == 0


def test_network_policy_mapper_and_registry():
    from klotse.k8s.resources import SPEC_BY_KEY, SPEC_BY_KIND, map_networkpolicy

    meta = client.V1ObjectMeta(name="np", namespace="ns", creation_timestamp=NOW)
    sel = client.V1LabelSelector(match_labels={"app": "web"}, match_expressions=[
        client.V1LabelSelectorRequirement(key="tier", operator="In", values=["a", "b"])])
    peer_rule = client.V1NetworkPolicyIngressRule(_from=[client.V1NetworkPolicyPeer(pod_selector=client.V1LabelSelector())])
    policy = client.V1NetworkPolicy(metadata=meta, spec=client.V1NetworkPolicySpec(
        pod_selector=sel, policy_types=["Ingress", "Egress"], ingress=[peer_rule, peer_rule],
        egress=[client.V1NetworkPolicyEgressRule()]))
    row = map_networkpolicy(policy)
    assert row.pod_selector == "app=web,tier In [a,b]"
    assert (row.policy_types, row.ingress, row.egress) == ("Ingress,Egress", 2, 1)
    # the classic default-deny: empty selector (every pod) + a listed type + no rules at all
    deny = map_networkpolicy(client.V1NetworkPolicy(metadata=meta, spec=client.V1NetworkPolicySpec(
        pod_selector=client.V1LabelSelector(), policy_types=["Ingress"])))
    assert (deny.pod_selector, deny.policy_types, deny.ingress, deny.egress) == ("<all pods>", "Ingress", 0, 0)
    assert row.raw["spec"]["podSelector"]["matchLabels"] == {"app": "web"}  # YAML detail / edit use the real object
    spec = SPEC_BY_KEY["networkpolicies"]
    assert spec.kind == "NetworkPolicy" and spec.namespaced and spec.editable and SPEC_BY_KIND["NetworkPolicy"] is spec
    assert [c[0] for c in spec.columns] == ["Name", "Namespace", "Pod Selector", "Policy Types", "Ingress rules",
                                            "Egress rules", "Age"]


def _overview_data(pending=0, failed=2, warnings=3):
    def pods(n, status):
        return [PodRow(f"{status.lower()}-{i}", "default", "0/1", status, 3, "n1", "1d",
                       {"kind": "Pod", "spec": {"containers": [{"name": "c"}]}}) for i in range(n)]

    def ev(i):
        return Row(f"w{i}", "default", {"kind": "Event"}, type="Warning", reason="BackOff", object="Pod/x",
                   message="m", count=1, last_seen="1m")

    return {"counts": {"pods": 9, "running": 7, "pending": pending, "failed": failed, "warnings": warnings},
            "events": [ev(0)],
            "lists": {"pending": pods(pending, "Pending"), "failed": pods(failed, "CrashLoopBackOff"),
                      "warnings": [ev(i) for i in range(warnings)]}}


def test_overview_cards_drill_down_on_click(qtbot):
    from PySide6.QtCore import Qt

    from klotse.ui.overview import CLUSTER_CARDS, OverviewView

    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    view.set_data(_overview_data(pending=0, failed=2, warnings=3))
    c = view.cards
    assert c["failed"]._clickable and c["warnings"]._clickable
    assert not c["pending"]._clickable and not c["pods"]._clickable and not c["running"]._clickable  # nothing behind them
    assert view.title.text() == "Recent events" and view.stack.currentWidget() is view.events_table and not view.back.isVisible()

    qtbot.mouseClick(c["pending"], Qt.LeftButton)  # a zero count does not open anything
    assert view.focus is None
    qtbot.mouseClick(c["failed"], Qt.LeftButton)
    assert view.focus == "failed" and view.stack.currentWidget() is view.pods_table
    assert view.title.text() == "Failed pods (2)" and view.pods_table.model.rowCount() == 2
    assert [r.status for r in view.pods_table.model.rows] == ["CrashLoopBackOff"] * 2
    assert view.back.isVisible()
    assert c["failed"].styleSheet().startswith(f"Card {{ border: 1px solid {ACCENT}")  # the open card is outlined
    assert c["warnings"].styleSheet().startswith(f"Card {{ border: 1px solid {BORDER}")  # the others keep the normal border

    qtbot.mouseClick(c["warnings"], Qt.LeftButton)  # switch straight to another card
    assert view.focus == "warnings" and view.stack.currentWidget() is view.warnings_table
    assert view.title.text() == "Warning events (3)" and view.warnings_table.model.rowCount() == 3
    qtbot.mouseClick(c["warnings"], Qt.LeftButton)  # clicking the selected card again closes it
    assert view.focus is None and view.title.text() == "Recent events" and view.stack.currentWidget() is view.events_table

    qtbot.mouseClick(c["failed"], Qt.LeftButton)
    qtbot.mouseClick(view.back, Qt.LeftButton)  # or use the button
    assert view.focus is None and not view.back.isVisible()


def test_overview_drill_down_follows_refreshes_and_clears_when_resolved(qtbot):
    from PySide6.QtCore import Qt

    from klotse.ui.overview import CLUSTER_CARDS, OverviewView

    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    view.set_data(_overview_data(failed=2))
    qtbot.mouseClick(view.cards["failed"], Qt.LeftButton)
    view.set_data(_overview_data(failed=1))  # a refresh keeps the open list and updates it in place
    assert view.focus == "failed" and view.title.text() == "Failed pods (1)" and view.pods_table.model.rowCount() == 1
    view.set_data(_overview_data(failed=0))  # all fixed: the empty list closes itself
    assert view.focus is None and view.stack.currentWidget() is view.events_table and not view.cards["failed"]._clickable
    assert not view.back.isVisible() and view.title.text() == "Recent events"
    view.set_data({"counts": {"failed": 3}, "events": []})  # data without drill-down lists (older service) is tolerated
    assert not view.cards["failed"]._clickable


def test_workspace_overview_drill_down_pods_have_pod_actions(qtbot):
    from PySide6.QtCore import Qt

    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws.overview.cards["failed"].value.text() == "2", timeout=5000)
    qtbot.mouseClick(ws.overview.cards["failed"], Qt.LeftButton)
    assert ws.overview.pods_table.model.rowCount() == 2
    ws.overview.pods_table.view.selectRow(0)  # selecting a row shows its YAML in the details dock
    assert "kind: Pod" in ws.detail.toPlainText()
    row = ws.overview.pods_table.model.rows[0]
    ws.overview.pods_table.action_requested.emit("logs", row)  # the right-click Logs/Shell/Terminate actions work here too
    assert ws.log_tabs.count() == 1 and ws.log_tabs.tabText(0) == "Logs: broken-0"
    ws.shutdown()


def test_pod_bucket_and_keep_newest_helpers():
    from klotse.k8s.client import keep_newest, pod_bucket

    assert [pod_bucket(s) for s in ("Running", "Succeeded", "Completed", "Pending", "ContainerCreating", "Init:0/2",
                                    "Terminating", "CrashLoopBackOff", "ImagePullBackOff", "Failed", "Evicted")] == [
        "running", "done", "done", "pending", "pending", "pending", "pending", "failed", "failed", "failed", "failed"]
    heap = []
    for seq, ts in enumerate([5, 1, 9, 3, 7, 9]):  # ties on ts must not compare the payloads (objects here)
        keep_newest(heap, 3, ts, seq, object())
    assert sorted((t, s) for t, s, _ in heap) == [(7, 4), (9, 2), (9, 5)]  # the 3 newest survive; the bound holds
    assert len(heap) == 3


def test_crd_mapping_and_instances():
    from kubernetes import client as k

    from klotse.k8s.client import KubeService
    from klotse.k8s.resources import SPEC_BY_KEY, crd_version, map_custom_object

    crd = k.V1CustomResourceDefinition(
        metadata=k.V1ObjectMeta(name="widgets.example.com"),
        spec=k.V1CustomResourceDefinitionSpec(
            group="example.com", scope="Namespaced",
            names=k.V1CustomResourceDefinitionNames(kind="Widget", plural="widgets"),
            versions=[k.V1CustomResourceDefinitionVersion(name="v1alpha1", served=True, storage=False),
                      k.V1CustomResourceDefinitionVersion(name="v1", served=True, storage=True),
                      k.V1CustomResourceDefinitionVersion(name="v0", served=False, storage=False)]))
    row = SPEC_BY_KEY["crds"].mapper(crd)
    assert (row.group, row.kind, row.scope, row.plural, row.version) == ("example.com", "Widget", "Namespaced", "widgets", "v1")
    assert row.versions == "v1alpha1,v1" and crd_version(crd) == "v1"
    obj = map_custom_object({"metadata": {"name": "w1", "namespace": "ns", "creationTimestamp": "2020-01-01T00:00:00Z"}})
    assert (obj.name, obj.namespace) == ("w1", "ns") and obj.age.endswith("d") and obj.raw["metadata"]["name"] == "w1"

    calls = []

    def fake(*args, **kw):
        calls.append((args, kw["_continue"]))
        return {"items": [{"metadata": {"name": str(len(calls))}}], "metadata": {"continue": None if kw["_continue"] else "n"}}

    svc = KubeService()
    svc._custom = type("C", (), {"list_cluster_custom_object": staticmethod(fake),
                                 "list_namespaced_custom_object": staticmethod(fake)})()
    assert [r.name for r in svc.list_custom_objects("example.com", "v1", "widgets", True)] == ["1", "2"]
    assert calls[0][0] == ("example.com", "v1", "widgets")
    calls.clear()
    svc.list_custom_objects("example.com", "v1", "widgets", True, "team")
    assert calls[0][0] == ("example.com", "v1", "team", "widgets")


def test_workspace_browses_crd_instances(qtbot):
    from klotse.k8s.resources import Row

    svc = FakeService()
    asked = []
    svc.list_custom_objects = lambda *a: asked.append(a) or [Row("w1", "default", {"kind": "Widget"}, age="1d")]
    win = MainWindow(lambda: svc)
    qtbot.addWidget(win)
    open_active(win)
    ws = win.workspace("ctx")
    qtbot.waitUntil(lambda: ws._ready and not ws._loading, timeout=5000)
    crd = Row("widgets.example.com", "", {}, group="example.com", kind="Widget", scope="Namespaced",
              plural="widgets", version="v1", versions="v1", age="1d")
    ws.open_custom_resources(crd)
    ws.open_custom_resources(crd)  # second open focuses the same tab
    table = ws.views["cr:widgets.example.com"][0]
    qtbot.waitUntil(lambda: table.model.rowCount() == 1, timeout=5000)
    assert ws.tabs.tabText(ws.tabs.currentIndex()) == "Widget" and [c[1] for c in table.base_columns] == ["name", "namespace", "age"]
    assert asked[0] == ("example.com", "v1", "widgets", True, "default")
    table.view.selectRow(0)
    assert "kind: Widget" in ws.detail.toPlainText()
