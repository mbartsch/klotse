import threading
import zipfile
from types import SimpleNamespace

import pytest
import yaml
from PySide6.QtWidgets import QMessageBox

from klotse.k8s import diagnostics as dg
from klotse.ui.diagnostics_dialog import DiagnosticsDialog
from klotse.ui.workspace import ClusterWorkspace


def pod(name="web-1", node="n1", restarts=0, env_value="hunter2"):
    return {"metadata": {"name": name, "namespace": "shop", "managedFields": [{"x": 1}],
                         "annotations": {dg.LAST_APPLIED: '{"env":[{"name":"PASSWORD","value":"hunter2"}]}', "keep": "me"}},
            "spec": {"nodeName": node, "containers": [{"name": "app", "env": [{"name": "PASSWORD", "value": env_value},
                                                                              {"name": "FROM", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]},
                                                       {"name": "side"}]},
            "status": {"containerStatuses": [{"name": "app", "restartCount": restarts}, {"name": "side", "restartCount": 0}]}}


class Service:
    def __init__(self, fail=(), cancel=None):
        self.fail, self.cancel, self.logs_asked = set(fail), cancel, []
        self.touched = []

    def rows(self, *raws):
        return [SimpleNamespace(raw=r) for r in raws]

    def list_pods(self, ns):
        self.touched.append("pods")
        return self.rows(pod(), pod("web-2", node="n2", restarts=3))

    def list_deployments(self, ns):
        return self.rows({"metadata": {"name": "api", "namespace": "shop"}, "spec": {"template": {"spec": {"containers": [
            {"name": "c", "env": [{"name": "TOKEN", "value": "abc"}]}]}}}})

    def list_statefulsets(self, ns):
        return []

    def list_nodes(self):
        return self.rows({"metadata": {"name": "n1"}, "status": {"conditions": [{"type": "Ready", "status": "True", "reason": "KubeletReady"}],
                                                                   "capacity": {"cpu": "4"}, "allocatable": {"cpu": "3920m"}}},
                         {"metadata": {"name": "n9"}, "status": {}})

    def list_resource(self, key, ns=None):
        self.touched.append(key)
        if key in self.fail:
            raise PermissionError(f"{key} is forbidden")
        if key == "configmaps":
            return self.rows({"metadata": {"name": "cfg", "namespace": "shop"}, "data": {"db.password": "supersecret", "mode": "prod"}})
        if key == "events":
            return self.rows({"type": "Warning", "reason": "BackOff", "message": "Back-off restarting\nfailed container",
                              "involvedObject": {"kind": "Pod", "name": "web-1"}, "lastTimestamp": "2026-11-07T10:00:00Z", "count": 4},
                             {"type": "Normal", "reason": "Pulled", "message": "ok", "involvedObject": {"kind": "Pod", "name": "web-2"},
                              "lastTimestamp": "2026-11-07T09:00:00Z"})
        return []

    def read_pod_log(self, namespace, pod_name, container, tail, previous):
        self.logs_asked.append((pod_name, container, tail, previous))
        if self.cancel is not None:
            self.cancel.set()
        return f"log of {pod_name}/{container}{' previous' if previous else ''}\n"


def options(**kw):
    return {"logs": True, "tail": 100, "keep_env": False, **kw}


def test_clean_removes_noise_hides_env_and_configmap_values_and_keeps_the_original():
    original = pod()
    body = dg.clean(original, "pods")
    assert "managedFields" not in body["metadata"] and dg.LAST_APPLIED not in body["metadata"]["annotations"]
    env = body["spec"]["containers"][0]["env"]
    assert env[0]["value"] == dg.REDACTED and env[1]["valueFrom"]["secretKeyRef"]["name"] == "s"  # references are not values
    assert "hunter2" not in yaml.safe_dump(body)
    assert dg.clean(original, "pods", keep_env=True)["spec"]["containers"][0]["env"][0]["value"] == "hunter2"
    assert original["spec"]["containers"][0]["env"][0]["value"] == "hunter2" and "managedFields" in original["metadata"]
    cm = dg.clean({"metadata": {"name": "c"}, "data": {"k": "supersecret"}, "binaryData": {"b": "AAAA"}}, "configmaps")
    assert "supersecret" not in yaml.safe_dump(cm) and "k" in cm["data"] and "AAAA" not in yaml.safe_dump(cm)
    assert dg.clean({"metadata": {"annotations": {dg.LAST_APPLIED: "x"}}})["metadata"] == {}
    assert dg.clean(None) == {}


def test_text_helpers():
    assert dg.safe_name("a/b\\c:d") == "a_b_c_d" and dg.safe_name("") == "_" and len(dg.safe_name("x" * 500)) == 120
    text = dg.events_text([{"type": "Normal", "reason": "B", "involvedObject": {"kind": "Pod", "name": "p"}, "message": "two\nlines",
                            "lastTimestamp": "2026-02"}, {"type": "Warning", "reason": "A", "lastTimestamp": "2026-01", "message": "x"}])
    rows = text.splitlines()
    assert rows[0].startswith("TIME") and rows[1].startswith("2026-01") and "two lines" in rows[2]  # oldest first, one line each
    assert "Ready=True KubeletReady" in dg.nodes_text([{"metadata": {"name": "n1"}, "status": {"conditions": [
        {"type": "Ready", "status": "True", "reason": "KubeletReady"}]}}])


def test_the_bundle_has_everything_and_leaks_nothing(tmp_path):
    target = tmp_path / "out.zip"
    service = Service()
    progress = []
    result = dg.collect(service, "prod", "shop", str(target), options(), "1.2.3", progress.append)
    with zipfile.ZipFile(target) as z:
        names = set(z.namelist())
        assert {"README.txt", "events.txt", "nodes.txt", "objects/pods.yaml", "objects/deployments.yaml", "objects/configmaps.yaml",
                "logs/web-1_app.log", "logs/web-1_side.log", "logs/web-2_app.log", "logs/web-2_app.previous.log"} <= names
        assert "logs/web-1_app.previous.log" not in names  # no restarts, no previous log
        everything = "\n".join(z.read(n).decode() for n in names if not n.startswith("logs/"))
        readme = z.read("README.txt").decode()
        assert z.read("logs/web-2_app.previous.log").decode() == "log of web-2/app previous\n"
    for secret in ("hunter2", "supersecret", "abc"):
        assert secret not in everything
    assert "Cluster context: prod" in readme and "Namespace: shop" in readme and "Klotse 1.2.3" in readme and "Secrets: never read" in readme
    assert "n1" in everything and "n9" not in everything  # only nodes that run the pods
    assert "secrets" not in service.touched and result["counts"]["pods"] == 2 and result["counts"]["logs"] == 5
    assert any("Reading logs of web-1/app" in p for p in progress)
    assert service.logs_asked[0] == ("web-1", "app", 100, False)


def test_options_logs_off_and_env_kept(tmp_path):
    target = tmp_path / "out.zip"
    service = Service()
    dg.collect(service, "c", "shop", str(target), options(logs=False, keep_env=True))
    with zipfile.ZipFile(target) as z:
        assert not [n for n in z.namelist() if n.startswith("logs/")] and "NOT collected" in z.read("README.txt").decode()
        assert "hunter2" in z.read("objects/pods.yaml").decode()
    assert service.logs_asked == []


def test_a_forbidden_kind_is_noted_not_fatal(tmp_path):
    target = tmp_path / "out.zip"
    result = dg.collect(Service(fail=["events", "services"]), "c", "shop", str(target), options(logs=False))
    assert any(n.startswith("events:") and "PermissionError" in n for n in result["notes"]) and any(n.startswith("services:") for n in result["notes"])
    with zipfile.ZipFile(target) as z:
        assert "Things that could not be collected" in z.read("README.txt").decode() and "objects/pods.yaml" in z.namelist()


def test_a_huge_log_keeps_its_end_and_says_so(tmp_path):
    class Big(Service):
        def read_pod_log(self, *a):
            return "x" * (dg.MAX_LOG_BYTES + 500) + "THE END\n"
    target = tmp_path / "out.zip"
    dg.collect(Big(), "c", "shop", str(target), options())
    with zipfile.ZipFile(target) as z:
        data = z.read("logs/web-1_app.log")
    assert data.startswith(b"[earlier lines cut") and data.endswith(b"THE END\n") and len(data) < dg.MAX_LOG_BYTES + 200


def test_cancel_stops_collecting(tmp_path):
    cancel = threading.Event()
    service = Service(cancel=cancel)
    with pytest.raises(dg.Cancelled):
        dg.collect(service, "c", "shop", str(tmp_path / "out.zip"), options(), cancel=cancel)
    assert len(service.logs_asked) == 1  # stopped right after the first log


def test_dialog_exports_on_its_thread_and_reports(qtbot, tmp_path):
    service = Service()
    dialog = DiagnosticsDialog(service, "prod", "shop", "1.0")
    qtbot.addWidget(dialog)
    target = tmp_path / "bundle.zip"
    dialog.path.setText(str(target))
    dialog.logs.setChecked(False)
    dialog.start()
    qtbot.waitUntil(lambda: "Saved" in dialog.status.text(), timeout=10000)
    assert target.exists() and dialog.start_button.isEnabled() and not dialog.cancel_button.isEnabled()
    assert "pods 2" in dialog.status.text()
    dialog.job.wait(3000)


def test_dialog_cancel_removes_the_unfinished_file(qtbot, tmp_path):
    cancel_seen = threading.Event()

    class Slow(Service):
        def read_pod_log(self, *a):
            cancel_seen.wait(5)
            return "x\n"
    service = Slow()
    dialog = DiagnosticsDialog(service, "prod", "shop")
    qtbot.addWidget(dialog)
    target = tmp_path / "bundle.zip"
    dialog.path.setText(str(target))
    dialog.start()
    qtbot.waitUntil(lambda: "Reading logs" in dialog.status.text(), timeout=10000)
    dialog.cancel()
    cancel_seen.set()
    qtbot.waitUntil(lambda: "Cancelled" in dialog.status.text(), timeout=10000)
    assert not target.exists()
    dialog.job.wait(3000)


def test_dialog_asks_before_replacing_and_needs_a_path(qtbot, tmp_path, monkeypatch):
    dialog = DiagnosticsDialog(Service(), "prod", "shop")
    qtbot.addWidget(dialog)
    dialog.path.setText("")
    assert dialog.start() is None and "Choose where" in dialog.status.text()
    existing = tmp_path / "old.zip"
    existing.write_bytes(b"old")
    dialog.path.setText(str(existing))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    assert dialog.start() is None and existing.read_bytes() == b"old"


def test_workspace_needs_one_namespace(qtbot, monkeypatch):
    told = []
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    ws.set_namespace("All namespaces")
    assert ws.export_diagnostics() is None and "one namespace" in told[0]
    dialog = ws.export_diagnostics("shop")
    assert dialog.namespace == "shop" and dialog.windowTitle() == "Export diagnostics"
    ws.shutdown()


def test_the_selected_namespace_in_the_namespaces_list_is_used_without_being_in_it(qtbot, monkeypatch):
    from klotse.k8s.models import NamespaceRow
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.set_namespace("All namespaces")
    ws.ns_table.set_rows([NamespaceRow("shop", "Active", "1d", ""), NamespaceRow("blog", "Active", "1d", "")])
    ws.foreground_key = lambda: "namespaces"
    ws.ns_table.view.selectRow(1)
    dialog = ws.export_diagnostics()
    assert dialog is not None and dialog.namespace == ws.ns_table.selected_rows()[0].name
    dialog.close()
    ws.shutdown()


def test_without_a_selection_or_a_namespace_it_offers_a_pick_list(qtbot, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.set_namespace("All namespaces")
    ws._ns_names = ["shop", "blog"]
    asked = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (asked.append(a[3]) or ("blog", True)))
    dialog = ws.export_diagnostics()
    assert asked == [["shop", "blog"]] and dialog.namespace == "blog"
    dialog.close()
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False))
    assert ws.export_diagnostics() is None  # cancelled: nothing opens
    ws.shutdown()


def test_the_toolbar_namespace_still_wins_over_a_pick_list(qtbot, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws._fill_namespace_combo(["shop"], "shop")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    dialog = ws.export_diagnostics()
    assert dialog.namespace == "shop"
    dialog.close()
    ws.shutdown()
