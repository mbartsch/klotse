"""Read-only mode per cluster: the mark is kept, the workspace refuses and explains, and the service refuses underneath."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService, ReadOnlyError
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.read_only import ReadOnlyClusters
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService, open_active
from test_plugin_actions import Acts, answer, node, workspace as plugin_workspace


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


# ---- the saved mark ----------------------------------------------------------------------------------------------
def test_the_mark_is_kept_per_context_and_survives_a_restart(tmp_path):
    marks = ReadOnlyClusters(store(tmp_path))
    assert marks.contexts() == [] and not marks.is_read_only("prod")
    marks.set_read_only("prod", True)
    marks.set_read_only("prod-eu", True)
    marks.set_read_only("prod", True)  # again: no duplicate
    assert ReadOnlyClusters(store(tmp_path)).contexts() == ["prod-eu", "prod"]
    marks.set_read_only("prod-eu", False)
    again = ReadOnlyClusters(store(tmp_path))
    assert again.contexts() == ["prod"] and again.is_read_only("prod") and not again.is_read_only("prod-eu")


def test_a_failed_save_is_an_error_not_a_silent_loss(tmp_path):
    settings = store(tmp_path)
    marks = ReadOnlyClusters(settings)
    settings.status = lambda: QSettings.AccessError
    with pytest.raises(OSError):
        marks.set_read_only("prod", True)


# ---- the service refuses -----------------------------------------------------------------------------------------
MUTATING = [("create_namespace", ({},)), ("create", ("Pod", {})), ("patch_secret", ("ns", "s", {})), ("replace", ("Pod", {})),
            ("scale", ("Deployment", "a", "ns", 1)), ("restart", ("Deployment", "a", "ns")), ("delete_pod", ("a", "ns")),
            ("delete_resource", ("Role", "a", "ns", "u")), ("cordon_node", ("n", True)), ("delete_node", ("n", "u"))]


@pytest.mark.parametrize("name, args", MUTATING)
def test_every_change_is_refused_while_read_only(name, args):
    svc = object.__new__(KubeService)
    svc.read_only = True
    with pytest.raises(ReadOnlyError, match="read-only"):
        getattr(svc, name)(*args)


def test_a_cluster_that_is_not_read_only_works_as_before():
    calls = []
    svc = object.__new__(KubeService)
    svc.read_only = False
    svc._apps = SimpleNamespace(patch_namespaced_deployment=lambda *a: calls.append(a))
    svc.restart("Deployment", "a", "ns")
    assert calls[0][:2] == ("a", "ns")


def test_apply_is_refused_but_a_dry__run_check_still_works():
    svc = object.__new__(KubeService)
    svc.read_only = True
    seen = []
    svc._dynamic_client = lambda: SimpleNamespace(
        resources=SimpleNamespace(get=lambda **kw: SimpleNamespace(namespaced=True)),
        server_side_apply=lambda *a, **kw: seen.append(kw))
    doc = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}}
    with pytest.raises(ReadOnlyError):
        svc.apply_documents([doc], "ns")
    assert svc.apply_documents([doc], "ns", dry_run=True)[0][3] is True and seen[0]["dry_run"] == "All"


# ---- the workspace refuses and explains ---------------------------------------------------------------------------
def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "prod")
    qtbot.addWidget(ws)
    ws.resize(1400, 800)  # wide enough that the toolbar does not move the banner into its overflow menu
    ws.show()
    return ws


def told(monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: said.append(a[2])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("no question while read-only")))
    return said


def row(**extra):
    return SimpleNamespace(name="a", namespace="shop", raw={"metadata": {"uid": "u"}, "spec": {"replicas": 1}},
                           secret_data={}, **extra)


def test_the_banner_and_the_service_follow_the_mode(qtbot):
    ws = make_ws(qtbot)
    seen = []
    ws.read_only_changed.connect(seen.append)
    assert not ws.read_only_label.isVisible()
    ws.set_read_only(True)
    qtbot.waitUntil(ws.read_only_label.isVisible, timeout=3000)
    assert ws.read_only and ws.service.read_only is True
    assert "Read-only" in ws.read_only_label.text() and "🔒" in ws.read_only_label.text()  # symbol and words
    ws.set_read_only(False)
    qtbot.waitUntil(lambda: not ws.read_only_label.isVisible(), timeout=3000)
    assert not ws.read_only and ws.service.read_only is False and seen == [True, False]


@pytest.mark.parametrize("call", [
    lambda ws: ws.edit_resource("Deployment", row()),
    lambda ws: ws.create_resource("Deployment"),
    lambda ws: ws.delete_resource("Role", row()),
    lambda ws: ws.scale_resource("Deployment", row()),
    lambda ws: ws.restart_resource("Deployment", row()),
    lambda ws: ws.bulk_action("Deployment", "restart", [row(), row()]),
    lambda ws: ws.bulk_action("Pod", "terminate", [row(), row()]),
    lambda ws: ws.new_namespace(),
    lambda ws: ws.node_action("cordon", row()),
    lambda ws: ws.node_action("drain", row()),
    lambda ws: ws.node_action("node-shell", row()),
    lambda ws: ws.node_action("delete-node", row()),
    lambda ws: ws.pod_action("terminate", row()),
    lambda ws: ws.pod_action("force-terminate", row()),
    lambda ws: ws.pod_action("shell", row()),
    lambda ws: ws.pod_action("attach", row()),
    lambda ws: ws._container_action("shell", row(), "c")])
def test_every_way_to_change_something_is_refused_with_an_explanation(qtbot, monkeypatch, call):
    touched = []
    ws = make_ws(qtbot, **{name: (lambda *a, **k: touched.append(name)) for name in
                           ("scale", "restart", "delete_resource", "delete_pod", "replace", "create", "cordon_node",
                            "delete_node", "create_namespace")})
    ws._launch_terminal = lambda *a: touched.append("terminal")
    ws.set_read_only(True)
    said = told(monkeypatch)
    call(ws)
    assert touched == [] and len(said) == 1 and "prod is in read-only mode" in said[0] and "View > Read-only mode" in said[0]


def test_looking_is_still_allowed(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.set_read_only(True)
    said = told(monkeypatch)
    opened = []
    ws.open_node_pods = lambda *a: opened.append("node-pods")
    ws.open_pod_tab = lambda *a: opened.append("containers")
    ws.open_logs = lambda *a: opened.append("logs")
    ws.node_action("node-pods", row())
    ws.pod_action("containers", row())
    ws.pod_action("logs", row())
    ws._container_action("logs", row(), "c")
    assert opened == ["node-pods", "containers", "logs", "logs"] and said == []


def test_a_dangerous_plugin_action_is_refused_but_a_harmless_one_runs(qtbot, tmp_path, monkeypatch):
    plugin = Acts()
    ws, _, _ = plugin_workspace(qtbot, tmp_path, plugin)
    ws.set_read_only(True)
    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: said.append(a[2])))
    answer(monkeypatch, QMessageBox.Yes)
    ws.plugin_runner.run("acts", "kill", [node("n1")], "Node")
    assert plugin.requests == [] and "read-only" in said[0]
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    assert len(plugin.requests) == 1


def test_apply_yaml_can_check_but_not_apply(qtbot):
    ws = make_ws(qtbot, apply_documents=lambda *a: [("ConfigMap", "default", "c", True, "valid")])
    ws.set_read_only(True)
    d = ws.apply_dialog()
    qtbot.addWidget(d)
    sent = []
    d.run_requested.connect(lambda docs, dry: sent.append(dry))
    d.editor.setPlainText("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c\n")
    assert d.check_button.isEnabled() and not d.apply_button.isEnabled() and "read-only" in d.apply_button.toolTip()
    d.check_button.click()
    d.show_results([("ConfigMap", "default", "c", True, "valid")], dry_run=True)
    assert sent == [True] and not d.apply_button.isEnabled()  # still off after a check has finished


# ---- the window --------------------------------------------------------------------------------------------------
def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    return win


def test_the_view_menu_switches_the_cluster_in_front_and_remembers_it(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    assert win.set_read_only(True) is False  # no cluster open
    open_active(win)
    ws = win.clusters.currentWidget()
    assert not ws.read_only and win.clusters.tabText(0) == ws.context
    win._sync_read_only_action()
    assert win.read_only_action.isEnabled() and not win.read_only_action.isChecked()
    assert win.set_read_only(True) is True
    assert ws.read_only and win.clusters.tabText(0) == f"{ws.context} 🔒" and win.read_only_action.isChecked()
    assert ReadOnlyClusters(store(tmp_path)).is_read_only(ws.context)


def test_turning_it_off_asks_first_and_no_keeps_it_on(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    win.set_read_only(True)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert win.set_read_only(False) is False and ws.read_only and win.read_only_action.isChecked()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert win.set_read_only(False) is True
    assert not ws.read_only and win.clusters.tabText(0) == ws.context and not ReadOnlyClusters(store(tmp_path)).is_read_only(ws.context)


def test_a_cluster_marked_before_opens_read_only(qtbot, tmp_path):
    first = window(qtbot, tmp_path)
    open_active(first)
    context = first.clusters.currentWidget().context
    first.set_read_only(True)
    second = window(qtbot, tmp_path)  # a new start, same settings file
    open_active(second)
    assert second.clusters.currentWidget().read_only and second.clusters.tabText(0).endswith("🔒")
    assert second.clusters.currentWidget().context == context
