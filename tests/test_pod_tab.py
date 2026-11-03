"""Double-clicking a pod opens its own tab (containers, per-container actions, pod actions)."""
from types import SimpleNamespace as NS

from kubernetes.client.exceptions import ApiException
from PySide6.QtWidgets import QMessageBox

from kubeman.k8s.models import PodRow
from kubeman.ui.workspace import ClusterWorkspace


def pod(name="web", status="Running", restarts=0):
    return PodRow(name, "team", "1/2", status, restarts, "node-a", "1d", {
        "kind": "Pod", "metadata": {"name": name, "namespace": "team"},
        "spec": {"containers": [{"name": "app", "image": "app:1"}, {"name": "sidecar", "image": "proxy:2"}]},
        "status": {"containerStatuses": [
            {"name": "app", "ready": True, "restartCount": restarts, "state": {"running": {}}},
            {"name": "sidecar", "ready": False, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(NS(**service), "ctx")
    qtbot.addWidget(ws)
    ws.load_view = lambda key: None
    ws.show()
    ws._ready = True
    return ws


def test_double_click_pod_opens_tab_not_dialog(qtbot):
    ws = make_ws(qtbot)
    ws.pod_table.set_rows([pod()])
    ws.pod_table._on_double_click(ws.pod_table.proxy.index(0, 0))
    assert ws.foreground_key() == "pod:team/web" and ws.tabs.tabText(ws.tabs.currentIndex()) == "Pod: web"
    tab = ws._pod_tabs["pod:team/web"]
    assert [r.name for r in tab["table"].model.rows] == ["app", "sidecar"]
    assert tab["bar"].title.text() == "Pod team/web" and "Running on node-a" in tab["bar"].state.text()
    count = ws.tabs.count()
    ws.pod_table._on_double_click(ws.pod_table.proxy.index(0, 0))  # second double-click focuses the same tab
    assert ws.tabs.count() == count


def test_container_row_actions_target_that_container(qtbot, monkeypatch):
    from kubeman.ui import workspace as module

    ws = make_ws(qtbot)
    seen = []
    ws._container_action = lambda action, row, container: seen.append((action, row.name, container))
    ws.open_pod_tab(pod())
    table = ws._pod_tabs["pod:team/web"]["table"]
    rows = table.model.rows
    table.action_requested.emit("shell", rows[1])
    table._on_double_click(table.proxy.index(0, 0))  # double-click a container: its logs
    assert seen == [("shell", "web", "sidecar"), ("logs", "web", "app")]
    assert table.DOUBLE_CLICK["Container"] == "logs"


def test_pod_bar_actions(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    seen = []
    ws.pod_action = lambda action, row: seen.append((action, row.name))
    ws.edit_resource = lambda kind, row: seen.append(("edit", kind, row.name))
    ws.open_pod_tab(pod())
    bar = ws._pod_tabs["pod:team/web"]["bar"]
    for button in bar.buttons:
        button.click()
    assert seen == [("logs", "web"), ("edit", "Pod", "web"), ("terminate", "web"), ("force-terminate", "web")]


def test_tab_refreshes_containers_and_handles_deleted_pod(qtbot):
    state = {"pod": pod(restarts=0)}

    def read_pod(name, namespace):
        if state["pod"] is None:
            raise ApiException(status=404)
        return state["pod"]

    ws = make_ws(qtbot, read_pod=read_pod)
    ws.open_pod_tab(pod())
    key = "pod:team/web"
    fetch, scoped, sink = ws.loaders[key]
    assert scoped is False  # a pod tab ignores the namespace selector
    state["pod"] = pod(restarts=5)
    sink(fetch(None))  # what a reload does
    tab = ws._pod_tabs[key]
    assert tab["table"].model.rows[0].restarts == "5"

    state["pod"] = None
    assert fetch(None) is None
    sink(None)
    assert not any(b.isEnabled() for b in tab["bar"].buttons) and "no longer" in tab["bar"].state.text()
    assert len(tab["table"].model.rows) == 2  # last known containers stay visible

    state["pod"] = pod()  # a pod with the same name came back (e.g. StatefulSet): actions work again
    sink(fetch(None))
    assert all(b.isEnabled() for b in tab["bar"].buttons)


def test_other_errors_are_not_treated_as_deleted(qtbot):
    def read_pod(name, namespace):
        raise ApiException(status=500)

    ws = make_ws(qtbot, read_pod=read_pod)
    ws.open_pod_tab(pod())
    fetch = ws.loaders["pod:team/web"][0]
    try:
        fetch(None)
    except ApiException as e:
        assert e.status == 500
    else:
        raise AssertionError("a 500 must surface as an error")


def test_read_pod_service_maps_one_pod():
    from kubernetes import client as k

    from kubeman.k8s.client import KubeService

    obj = k.V1Pod(metadata=k.V1ObjectMeta(name="web", namespace="team"), spec=k.V1PodSpec(containers=[]),
                  status=k.V1PodStatus(phase="Running"))
    svc = KubeService()
    svc._core = NS(read_namespaced_pod=lambda name, ns, **kw: obj if (name, ns) == ("web", "team") else None)
    row = svc.read_pod("web", "team")
    assert (row.name, row.namespace, row.status) == ("web", "team", "Running") and row.raw["metadata"]["name"] == "web"


def test_container_context_menu_has_logs_shell_attach(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu

    ws = make_ws(qtbot)
    ws.open_pod_tab(pod())
    table = ws._pod_tabs["pod:team/web"]["table"]
    labels = []

    def grab():
        menu = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QMenu) and w.isVisible())
        labels.extend(a.text() for a in menu.actions() if not a.isSeparator())
        menu.close()

    QTimer.singleShot(200, grab)
    table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
    assert labels == ["Logs", "Connect (shell)…", "Attach…"]
