from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.selectors import selector_text, valid_selector
from klotse.ui import log_view
from klotse.ui.multi_log import MAX_STREAMS, MultiLogView
from klotse.ui.workspace import ClusterWorkspace


# ---- selectors -----------------------------------------------------------------------------------------------------
def test_selector_text_from_workloads_and_services():
    assert selector_text({"matchLabels": {"app": "web", "tier": "fe"}}) == "app=web,tier=fe"
    assert selector_text({"app": "web"}) == "app=web"  # a Service
    expr = {"matchLabels": {"a": "b"}, "matchExpressions": [
        {"key": "env", "operator": "In", "values": ["prod", "stage"]}, {"key": "x", "operator": "NotIn", "values": ["1"]},
        {"key": "has", "operator": "Exists"}, {"key": "gone", "operator": "DoesNotExist"}]}
    assert selector_text(expr) == "a=b,env in (prod,stage),x notin (1),has,!gone"
    assert selector_text({}) == "" and selector_text(None) == ""  # an empty selector would match every pod: callers must refuse


@pytest.mark.parametrize("text", ["app=web", "app=web,tier=fe", "env in (prod,stage),app", "!debug", "a.b/c!=d", "x==y"])
def test_good_selectors(text):
    assert valid_selector(text) is None


@pytest.mark.parametrize("text", ["", "   ", "app=we b", "=x", "env in (a b", "a b c", "k=v,,"])
def test_bad_selectors(text):
    assert valid_selector(text)


def test_service_lists_pods_by_selector_and_refuses_an_empty_one():
    seen = {}

    def pods(namespace, **kw):
        seen.update(kw, namespace=namespace)
        meta = lambda n: SimpleNamespace(name=n)
        return SimpleNamespace(items=[SimpleNamespace(metadata=meta("b"), spec=SimpleNamespace(containers=[SimpleNamespace(name="app")]),
                                                      status=SimpleNamespace(phase="Running")),
                                      SimpleNamespace(metadata=meta("a"), spec=SimpleNamespace(containers=[SimpleNamespace(name="x"), SimpleNamespace(name="y")]),
                                                      status=None)], metadata=None)
    service = KubeService.__new__(KubeService)
    service._core = SimpleNamespace(list_namespaced_pod=pods)
    assert service.pods_by_selector("shop", "app=web") == [("a", ["x", "y"], ""), ("b", ["app"], "Running")]
    assert seen["label_selector"] == "app=web" and seen["namespace"] == "shop"
    with pytest.raises(ValueError):
        service.pods_by_selector("shop", "  ")


def test_log_stream_options_for_since_and_not_following():
    seen = {}
    service = KubeService.__new__(KubeService)
    service._core = SimpleNamespace(read_namespaced_pod_log=lambda pod, ns, **kw: seen.update(kw) or "resp")
    service.open_log_stream("ns", "p", "c", 50, since_seconds=300, follow=False)
    assert seen["since_seconds"] == 300 and seen["follow"] is False
    seen.clear()
    service.open_log_stream("ns", "p", "c", 50)
    assert "since_seconds" not in seen and seen["follow"] is True


# ---- the view ------------------------------------------------------------------------------------------------------
class Stream:
    def __init__(self, data):
        self.data = data

    def stream(self, n):
        return iter(self.data)

    def close(self):
        pass


class Service:
    def __init__(self, lines):
        self.lines, self.opened = lines, []

    def open_log_stream(self, namespace, pod, container=None, tail=500, **options):
        self.opened.append((pod, container, tail, options))
        return Stream([("\n".join(self.lines.get((pod, container), [])) + "\n").encode()] if self.lines.get((pod, container)) else [])


def sync_run(fn, on_result, on_error=None):
    try:
        result = fn()
    except Exception as error:  # noqa: BLE001
        on_error(str(error))
    else:
        on_result(result)


def make(qtbot, pods, lines, **kw):
    service = Service(lines)
    view = MultiLogView(service, "shop", lambda text: pods, sync_run, selector=kw.pop("selector", "app=web"), **kw)
    qtbot.addWidget(view)
    return service, view


def settle(qtbot, view, count):
    """Wait for `count` real lines and for every stream to have closed (its end marker is a line too)."""
    qtbot.waitUntil(lambda: sum(1 for line in view.lines if "stream ended" not in line) >= count, timeout=5000)
    qtbot.waitUntil(lambda: sum(1 for line in view.lines if "stream ended" in line) == len(view.streams), timeout=5000)


def teardown(view):
    view.stop()
    log_view.shutdown_all()


def test_lines_of_every_pod_arrive_with_their_prefix(qtbot):
    lines = {("web-1", "app"): ["hello", "boom error"], ("web-2", "app"): ["hello again"], ("db-0", "pg"): ["a"], ("db-0", "side"): ["b"]}
    service, view = make(qtbot, [("web-1", ["app"]), ("web-2", ["app"]), ("db-0", ["pg", "side"])], lines)
    settle(qtbot, view, 5)
    text = set(view.lines)
    assert {"[web-1] hello", "[web-1] boom error", "[web-2] hello again", "[db-0/pg] a", "[db-0/side] b"} <= text
    qtbot.waitUntil(lambda: len(service.opened) == 4)
    assert view.container.count() == 1 + 3  # "All containers" and the three distinct names: app, pg, side
    teardown(view)


def test_regex_keeps_and_drops_lines_and_all_lines_are_kept(qtbot):
    lines = {("web-1", "app"): ["GET /a 200", "GET /b 500", "POST /c 200"]}
    _, view = make(qtbot, [("web-1", ["app"])], lines)
    settle(qtbot, view, 3)
    view.match.setText("get")
    view.refilter()
    assert view.visible_text().splitlines() == ["[web-1] GET /a 200", "[web-1] GET /b 500"]
    view.drop.setText(r"\b500\b")
    view.refilter()
    assert view.visible_text().splitlines() == ["[web-1] GET /a 200"]
    view.ignore_case.setChecked(False)
    view.refilter()
    assert view.visible_text() == ""  # "get" no longer matches "GET"
    view.match.setText("")
    view.drop.setText("")
    view.refilter()
    assert view.visible_text().splitlines() == ["[web-1] GET /a 200", "[web-1] GET /b 500", "[web-1] POST /c 200", "[web-1] [stream ended]"]
    # nothing was lost by filtering
    teardown(view)


def test_a_bad_expression_filters_nothing_and_is_flagged(qtbot):
    _, view = make(qtbot, [("web-1", ["app"])], {("web-1", "app"): ["x", "y"]})
    settle(qtbot, view, 2)
    view.match.setText("([")
    view.refilter()
    assert view.visible_text().splitlines() == ["[web-1] x", "[web-1] y", "[web-1] [stream ended]"]
    assert "valid regular expression" in view.match.toolTip()
    teardown(view)


def test_options_are_passed_to_the_streams(qtbot):
    service, view = make(qtbot, [("web-1", ["app"])], {})
    view.since.setCurrentIndex(3)  # last hour: restarts the streams
    view.timestamps.setChecked(True)
    view.follow.setChecked(False)
    before = len(service.opened)
    view.start()
    qtbot.waitUntil(lambda: len(service.opened) > before)
    pod, container, tail, options = service.opened[-1]
    assert (pod, container, tail) == ("web-1", "app", 200) and options == {"timestamps": True, "since_seconds": 3600, "follow": False}
    teardown(view)


def test_a_container_choice_limits_the_streams(qtbot):
    service, view = make(qtbot, [("db-0", ["pg", "side"])], {})
    qtbot.waitUntil(lambda: len(service.opened) == 2)
    before = len(service.opened)
    view.container.setCurrentIndex(view.container.findData("pg"))
    qtbot.waitUntil(lambda: len(service.opened) > before)
    qtbot.wait(150)
    assert {o[:2] for o in service.opened[before:]} == {("db-0", "pg")}  # only that container is followed now
    teardown(view)


def test_new_pods_found_while_following_get_a_stream_and_old_ones_are_kept(qtbot):
    pods = [("web-1", ["app"])]
    service = Service({})
    view = MultiLogView(service, "shop", lambda text: pods, sync_run, selector="app=web")
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: len(service.opened) == 1)
    pods.append(("web-2", ["app"]))
    view._poll_pods()
    qtbot.waitUntil(lambda: len(service.opened) == 2)
    assert sorted(o[0] for o in service.opened) == ["web-1", "web-2"]
    view._poll_pods()
    qtbot.wait(150)
    assert len(service.opened) == 2  # nothing is opened twice
    view.follow.setChecked(False)
    pods.append(("web-3", ["app"]))
    view._poll_pods()
    qtbot.wait(150)
    assert len(service.opened) == 2 and set(view.streams) == {("web-1", "app"), ("web-2", "app")}  # not following: no new lookups
    teardown(view)


def test_only_the_first_streams_are_followed_and_the_status_says_so(qtbot):
    pods = [(f"p{i:02d}", ["c"]) for i in range(MAX_STREAMS + 5)]
    service, view = make(qtbot, pods, {})
    qtbot.waitUntil(lambda: len(service.opened) == MAX_STREAMS, timeout=5000)
    assert len(view.streams) == MAX_STREAMS and "only the first" in view.status.text()
    teardown(view)


def test_a_bad_selector_asks_for_a_fix_and_starts_nothing(qtbot):
    service, view = make(qtbot, [("web-1", ["app"])], {}, selector="app=we b")
    qtbot.wait(150)
    assert service.opened == [] and "Not a valid selector" in view.status.text()
    view.selector.setText("app=web")
    view.start()
    qtbot.waitUntil(lambda: len(service.opened) == 1)
    teardown(view)


def test_a_failing_lookup_is_shown(qtbot):
    def resolver(text):
        raise RuntimeError("forbidden")
    view = MultiLogView(Service({}), "shop", resolver, sync_run, selector="app=web")
    qtbot.addWidget(view)
    assert "Could not list the pods: forbidden" in view.status.text()
    teardown(view)


def test_save_writes_what_is_shown(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    _, view = make(qtbot, [("web-1", ["app"])], {("web-1", "app"): ["keep me", "drop me"]})
    settle(qtbot, view, 2)
    view.match.setText("keep")
    view.refilter()
    target = tmp_path / "out.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    assert view.save() == str(target)
    assert target.read_text(encoding="utf-8") == "[web-1] keep me\n"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    assert view.save() is None
    teardown(view)


def test_closing_the_view_stops_everything(qtbot):
    pods = [("web-1", ["app"])]
    service = Service({})
    view = MultiLogView(service, "shop", lambda text: pods, sync_run, selector="app=web")
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: len(service.opened) == 1)
    view.stop()
    pods.append(("web-2", ["app"]))
    view._poll_pods()
    qtbot.wait(150)
    assert len(service.opened) == 1 and view.streams == {}
    log_view.shutdown_all()


# ---- the workspace -------------------------------------------------------------------------------------------------
def deployment_row(selector=None):
    spec = {"selector": selector} if selector is not None else {}
    return SimpleNamespace(name="api", namespace="shop", raw={"spec": spec})


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_a_workload_opens_one_tab_with_its_selector_and_reuses_it(qtbot):
    ws = make_ws(qtbot, pods_by_selector=lambda ns, text: [("api-1", ["app"], "Running")],
                 open_log_stream=lambda *a, **k: Stream([]))
    view = ws.multi_logs("Deployment", deployment_row({"matchLabels": {"app": "api"}}))
    assert view.selector.text() == "app=api" and ws.log_tabs.tabText(0) == "Logs: Deployment api"
    assert ws.multi_logs("Deployment", deployment_row({"matchLabels": {"app": "api"}})) is view and ws.log_tabs.count() == 1
    qtbot.waitUntil(lambda: bool(view.streams), timeout=5000)
    ws.shutdown()
    assert view._closed
    log_view.shutdown_all()
    qtbot.waitUntil(lambda: not ws._workers)


def test_a_workload_without_a_selector_is_explained(qtbot, monkeypatch):
    told = []
    ws = make_ws(qtbot)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    assert ws.multi_logs("Job", deployment_row()) is None and "no label selector" in told[0] and ws.log_tabs.count() == 0


def test_several_pods_open_together_but_only_from_one_namespace(qtbot, monkeypatch):
    told = []
    ws = make_ws(qtbot, open_log_stream=lambda *a, **k: Stream([]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))

    def pod(name, ns):
        return SimpleNamespace(name=name, namespace=ns, raw={"spec": {"containers": [{"name": "app"}]}, "status": {}})
    assert ws.multi_logs("Pod", [pod("a", "x"), pod("b", "y")]) is None and "one namespace" in told[0]
    view = ws.multi_logs("Pod", [pod("a", "x"), pod("b", "x")])
    assert view.fixed and not view.selector.isVisibleTo(view) and ws.log_tabs.tabText(0) == "Logs: 2 pods"
    qtbot.waitUntil(lambda: len(view.streams) == 2, timeout=5000)
    ws.shutdown()
    log_view.shutdown_all()
    qtbot.waitUntil(lambda: not ws._workers)


def test_logs_by_selector_asks_checks_and_opens(qtbot, monkeypatch):
    told = []
    ws = make_ws(qtbot, pods_by_selector=lambda ns, text: [], open_log_stream=lambda *a, **k: Stream([]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("app=we b", True)))
    assert ws.logs_by_selector() is None and "Not a valid selector" in told[0]
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    assert ws.logs_by_selector() is None
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("app=web", True)))
    view = ws.logs_by_selector()
    assert view.selector.text() == "app=web" and view.namespace == "default"
    ws.shutdown()
    log_view.shutdown_all()
    qtbot.waitUntil(lambda: not ws._workers)


def test_menus_offer_the_entries(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMenu
    from klotse.ui.resource_table import ResourceTable
    for kind, expected in (("Deployment", True), ("Service", True), ("Pod", False), ("ConfigMap", False)):
        table = ResourceTable([("Name", "name")], kind)
        qtbot.addWidget(table)
        table.set_rows([SimpleNamespace(name="x", namespace="n", raw={"spec": {}, "metadata": {}})])
        seen = []

        def read():
            menu = QApplication.activePopupWidget()
            seen.extend(a.text() for a in menu.actions())
            menu.close()
        QTimer.singleShot(200, read)
        table.view.selectRow(0)
        table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
        assert ("Logs of all pods…" in seen) is expected
    pods = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(pods)
    got = []
    pods.multi_logs_requested.connect(lambda kind, rows: got.append((kind, len(rows))))
    menu = QMenu()
    pods._bulk_menu(menu, [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()])
    next(a for a in menu.actions() if a.text() == "Logs of 3 pods…").trigger()
    assert got == [("Pod", 3)]
