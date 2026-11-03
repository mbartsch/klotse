"""Change watches: the foreground view reloads when the cluster reports a change."""
from threading import Event
from types import SimpleNamespace as NS

from kubeman.k8s import client as module
from kubeman.k8s.client import KubeService
from kubeman.ui.workspace import ClusterWorkspace


def obj(version):
    return NS(metadata=NS(resource_version=version))


def test_watch_targets_per_view():
    core = NS(list_node=1, list_namespace=2, list_pod_for_all_namespaces=3, list_namespaced_pod=4)
    svc = KubeService()
    svc._core = core
    svc._apps = NS(list_deployment_for_all_namespaces=5, list_namespaced_deployment=6)
    svc._apis = {"net": NS(list_ingress_for_all_namespaces=7, list_namespaced_ingress=8),
                 "storage": NS(list_storage_class=9)}
    assert svc.watch_targets("nodes") == [(1, ())]
    assert svc.watch_targets("namespaces") == [(2, ())]
    assert svc.watch_targets("pods") == [(3, ())] and svc.watch_targets("pods", "team") == [(4, ("team",))]
    assert svc.watch_targets("overview") == [(1, ()), (3, ())] and svc.watch_targets("workloads", "t") == [(4, ("t",))]
    assert svc.watch_targets("deployments", "t") == [(6, ("t",))]
    assert svc.watch_targets("ingresses") == [(7, ())] and svc.watch_targets("storageclasses") == [(9, ())]
    assert svc.watch_targets("no-such-view") == []


def run_stream(monkeypatch, scripts, stop=None):
    """Drive watch_changes with scripted watch streams; returns (yielded types, kwargs of each stream call)."""
    stop = stop or Event()
    calls = []

    class FakeWatch:
        def stream(self, fn, *args, **kwargs):
            calls.append(kwargs)
            if not scripts:
                stop.set()
                return iter(())
            return iter(scripts.pop(0))

        def stop(self):
            pass

    monkeypatch.setattr(module.watch, "Watch", FakeWatch)
    fn = lambda *a, **kw: obj("100")  # noqa: E731 - the initial list call that provides the starting version
    return list(KubeService.watch_changes(fn, (), stop, timeout=1)), calls


def test_watch_changes_resumes_from_last_version_and_skips_bookmarks(monkeypatch):
    scripts = [[{"type": "ADDED", "object": obj("101")}, {"type": "BOOKMARK", "object": obj("102")},
                {"type": "MODIFIED", "object": obj("103")}],
               [{"type": "DELETED", "object": obj("104")}]]
    seen, calls = run_stream(monkeypatch, scripts)
    assert seen == ["ADDED", "MODIFIED", "DELETED"]
    assert [c["resource_version"] for c in calls[:2]] == ["100", "103"]  # starts at the list version, then resumes
    assert all(c["timeout_seconds"] == 1 for c in calls)


def test_watch_changes_restarts_from_fresh_list_after_error_event(monkeypatch):
    scripts = [[{"type": "ERROR", "object": {"code": 410}}], [{"type": "ADDED", "object": obj("7")}]]
    seen, calls = run_stream(monkeypatch, scripts)
    assert seen == ["ADDED"] and [c["resource_version"] for c in calls[:2]] == ["100", "100"]


def test_watch_changes_stops_when_flag_set(monkeypatch):
    stop = Event()
    stop.set()
    seen, calls = run_stream(monkeypatch, [[{"type": "ADDED", "object": obj("1")}]], stop)
    assert seen == [] and calls == []


class WatchService:
    """Records which watches the workspace asks for; streams block until stopped."""

    def __init__(self):
        self.targets, self.started, self.release = [], [], []

    def watch_targets(self, key, namespace=None):
        self.targets.append((key, namespace))
        return [(key, (namespace,))]

    def watch_changes(self, fn, args, stop):
        self.started.append((fn, args))
        stop.wait(5)
        return iter(())


def ws_with_watch(qtbot):
    svc = WatchService()
    ws = ClusterWorkspace(svc, "ctx")
    qtbot.addWidget(ws)
    loads = []
    ws.load_view = loads.append
    ws.show()
    ws.open_view("pods")
    ws.open_view("nodes")
    ws._ready = True
    ws._sync_watch()
    return ws, svc, loads


def test_only_the_foreground_view_is_watched(qtbot):
    ws, svc, _ = ws_with_watch(qtbot)
    assert svc.targets == [("nodes", None)]
    ws.tabs.setCurrentWidget(ws.views["pods"][0])
    assert svc.targets[-1] == ("pods", None) and len(ws._watchers) == 1
    ws.ns_combo.addItem("team")
    ws.ns_combo.setCurrentText("team")
    ws.load_scoped()  # the watch follows the namespace for scoped views
    assert svc.targets[-1] == ("pods", "team")
    ws.tabs.setCurrentWidget(ws.views["nodes"][0])  # cluster-scoped views ignore the namespace
    assert svc.targets[-1] == ("nodes", None)
    ws.shutdown()
    assert ws._watchers == []


def test_watches_stop_when_hidden_or_toggled_off(qtbot):
    ws, svc, _ = ws_with_watch(qtbot)
    assert len(ws._watchers) == 1
    ws.hide()
    assert ws._watchers == [] and ws._watched is None
    ws.show()
    assert len(ws._watchers) == 1
    ws.auto_action.setChecked(False)
    assert ws._watchers == []
    ws.auto_action.setChecked(True)
    assert len(ws._watchers) == 1
    ws.shutdown()


def test_change_event_reloads_foreground_once_after_debounce(qtbot):
    ws, svc, loads = ws_with_watch(qtbot)
    loads.clear()
    for _ in range(5):
        ws._watchers[0].changed.emit()  # a burst of events
    qtbot.waitUntil(lambda: loads == ["nodes"], timeout=3000)
    qtbot.wait(1000)
    assert loads == ["nodes"]  # one reload for the whole burst
    ws.shutdown()


def test_service_without_watch_support_is_just_polled(qtbot):
    ws = ClusterWorkspace(NS(), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    ws.open_view("pods")
    ws._ready = True
    ws._sync_watch()
    assert ws._watchers == []
