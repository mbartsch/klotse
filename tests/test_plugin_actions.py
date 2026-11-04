import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QMessageBox

from klotse.k8s.client import KubeService
from klotse.k8s.models import NodeRow
from klotse.plugins import (API_VERSION, Action, Copy, Message, OpenUrl, Plugin, Reload, ShowText, Terminal)
from klotse.plugins.manager import Loaded, PluginManager, _as_plugin
from klotse.ui import plugin_actions
from klotse.ui.plugins_panel import PluginsPanel
from klotse.ui.workspace import ClusterWorkspace
from test_plugins import node, store


class Acts(Plugin):
    id = "acts"
    name = "Acting plugin"
    version = "1"
    actions = (
        Action("copy", "Copy name"),
        Action("kill", "Kill", kinds=("Node", "Pod"), multi=True, dangerous=True),
        Action("zone-a", "Only in zone a", kinds=("Node",)),
        Action("pods", "Pod thing", kinds=("Pod",)),
    )

    def __init__(self):
        self.requests = []
        self.result = None

    def applies(self, action, resource):
        if action.key == "zone-a":
            return resource.raw["metadata"]["labels"]["zone"] == "a"
        return True

    def run(self, request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def manager_with(tmp_path, *plugins):
    manager = PluginManager(store(tmp_path), folder=None, bundled=(), entry_points=False)
    manager.loaded = [Loaded(p.id, "bundled", p.id, p) for p in plugins]
    for plugin in plugins:
        manager.set_enabled(plugin.id, True)
    return manager


def workspace(qtbot, tmp_path, *plugins):
    manager = manager_with(tmp_path, *plugins)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    messages = []
    ws.message.connect(messages.append)

    def run_now(fn, on_result, *args, on_error=None):  # the worker, run on the spot
        try:
            result = fn(*args)
        except Exception as error:
            on_error(str(error))
        else:
            on_result(result)

    ws._run = run_now
    return ws, manager, messages


def answer(monkeypatch, value):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or value))
    return asked


# ---- the definitions ---------------------------------------------------------------------
def test_actions_and_the_required_api_are_validated():
    class DupKeys(Plugin):
        id = "dup"
        actions = (Action("a", "A"), Action("a", "B"))

    class NoKinds(Plugin):
        id = "nokinds"
        actions = (Action("a", "A", kinds=()),)

    class NotAnAction(Plugin):
        id = "notanaction"
        actions = ("x",)

    class TooNew(Plugin):
        id = "toonew"
        requires_api = API_VERSION + 1

    for bad in (DupKeys, NoKinds, NotAnAction):
        with pytest.raises((ValueError, TypeError)):
            _as_plugin(bad)
    with pytest.raises(ValueError, match="needs plugin API"):
        _as_plugin(TooNew)
    assert _as_plugin(Acts).actions[0].key == "copy" and API_VERSION == 6


def test_actions_for_follows_kind_multi_selection_and_enabling(tmp_path):
    manager = manager_with(tmp_path, Acts())
    assert [a.key for _, actions in manager.actions_for("Node") for a in actions] == ["copy", "kill", "zone-a"]
    assert [a.key for _, actions in manager.actions_for("Node", multi=True) for a in actions] == ["kill"]
    assert [a.key for _, actions in manager.actions_for("Pod") for a in actions] == ["kill", "pods"]
    assert manager.actions_for("Deployment") == []
    manager.set_enabled("acts", False)
    assert manager.actions_for("Node") == []


# ---- the menu ---------------------------------------------------------------------------
def menu_for(qtbot, ws, kind_table, rows):
    menu = QMenu()
    qtbot.addWidget(menu)
    kind_table._plugin_menu(menu, rows)
    return menu


def test_a_plugin_with_several_actions_gets_a_submenu_and_dangerous_ones_are_marked(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    menu = menu_for(qtbot, ws, ws.node_table, [node("n1", "a")])
    top = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in top] == ["Acting plugin"] and top[0].menu() is not None
    assert [a.text() for a in top[0].menu().actions()] == ["Copy name", "⚠ Kill", "Only in zone a"]
    assert menu.actions()[0].isSeparator()  # a separator sets the plugin entries apart from the built-in ones


def test_a_plugin_with_one_action_is_listed_flat(qtbot, tmp_path):
    class One(Acts):
        id = "one"
        actions = (Action("only", "Do the thing"),)

    ws, _, _ = workspace(qtbot, tmp_path, One())
    menu = menu_for(qtbot, ws, ws.node_table, [node("n1")])
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ["Do the thing"]


def test_the_menu_depends_on_the_kind_the_selection_and_applies(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    nodes = [node("n1", "a"), node("n2", "b")]
    only_a = menu_for(qtbot, ws, ws.node_table, [nodes[0]])
    assert "Only in zone a" in [a.text() for a in only_a.actions()[1].menu().actions()]
    only_b = menu_for(qtbot, ws, ws.node_table, [nodes[1]])
    assert "Only in zone a" not in [a.text() for a in only_b.actions()[1].menu().actions()]  # applies() said no
    several = menu_for(qtbot, ws, ws.node_table, nodes)
    assert [a.text() for a in several.actions() if not a.isSeparator()] == ["⚠ Kill"]  # only multi actions
    pods = menu_for(qtbot, ws, ws.pod_table, [nodes[0]])
    assert [a.text() for a in pods.actions()[1].menu().actions()] == ["⚠ Kill", "Pod thing"]


def test_a_plugin_whose_applies_raises_just_loses_the_entry(qtbot, tmp_path):
    class Raising(Acts):
        id = "raising"

        def applies(self, action, resource):
            raise RuntimeError("bug")

    ws, _, _ = workspace(qtbot, tmp_path, Raising())
    menu = menu_for(qtbot, ws, ws.node_table, [node("n1")])
    assert menu.isEmpty()


def test_choosing_an_entry_asks_for_that_action_with_the_rows(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    rows = [node("n1", "a")]
    asked = []
    ws.node_table.plugin_action_requested.connect(lambda *args: asked.append(args))
    menu = menu_for(qtbot, ws, ws.node_table, rows)
    menu.actions()[1].menu().actions()[0].trigger()
    assert asked == [("acts", "copy", rows)]


def test_tables_without_plugins_or_not_customizable_have_no_entries(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    assert ws.overview.pods_table.plugin_actions is None  # the Overview's small drill-down tables
    plain = ClusterWorkspace(None, "ctx")
    qtbot.addWidget(plain)
    assert plain.node_table.plugin_actions is None and plain.plugin_runner is None


# ---- running ------------------------------------------------------------------------------
def test_an_action_runs_with_everything_a_plugin_needs(qtbot, tmp_path):
    plugin = Acts()
    ws, manager, messages = workspace(qtbot, tmp_path, plugin)
    manager.set_setting("acts", "k", "v")
    rows = [node("n1", "a")]
    ws.plugin_runner.run("acts", "copy", rows, "Node")
    request = plugin.requests[0]
    assert (request.plugin_id, request.action, request.kind, request.context) == ("acts", "copy", "Node", "ctx")
    assert [r.name for r in request.resources] == ["n1"] and request.resources[0].raw["metadata"]["uid"] == "uid-n1"
    assert request.kubectl[:1] == ("kubectl",) and request.kubectl[-2:] == ("--context", "ctx")
    request.log("progress line")
    qtbot.waitUntil(lambda: "progress line" in ws.plugin_runner._output_tab.toPlainText())
    assert "Acting plugin: Copy name on n1" in ws.plugin_runner._output_tab.toPlainText()  # the activity line
    with pytest.raises(RuntimeError, match="not connected"):
        request.api_client()


def test_api_client_gives_the_connected_clusters_client():
    service = KubeService()
    with pytest.raises(RuntimeError):
        service.api_client()
    service._api_client = object()
    assert service.api_client() is service._api_client


def test_confirmation_is_asked_for_dangerous_actions_and_a_no_runs_nothing(qtbot, tmp_path, monkeypatch):
    plugin = Acts()
    ws, _, _ = workspace(qtbot, tmp_path, plugin)
    asked = answer(monkeypatch, QMessageBox.No)
    rows = [node("n1"), node("n2")]
    ws.plugin_runner.run("acts", "kill", rows, "Node")
    assert plugin.requests == [] and "n1, n2" in asked[0] and "Acting plugin" in asked[0]
    answer(monkeypatch, QMessageBox.Yes)
    ws.plugin_runner.run("acts", "kill", rows, "Node")
    assert [r.name for r in plugin.requests[0].resources] == ["n1", "n2"]
    ws.plugin_runner.run("acts", "copy", rows[:1], "Node")  # not dangerous: no question
    assert len(plugin.requests) == 2


def test_an_actions_own_confirmation_text_is_used(qtbot, tmp_path, monkeypatch):
    class Asks(Acts):
        id = "asks"
        actions = (Action("go", "Go", confirm="Really go?"),)

    ws, _, _ = workspace(qtbot, tmp_path, Asks())
    asked = answer(monkeypatch, QMessageBox.No)
    ws.plugin_runner.run("asks", "go", [node("n1")], "Node")
    assert asked == ["Really go?"]


def test_many_resources_are_abbreviated_in_the_question_and_the_activity_line():
    names = [type("R", (), {"key": f"n{i}"})() for i in range(8)]
    assert plugin_actions.describe(names) == "n0, n1, n2, n3, n4 and 3 more"


# ---- what a plugin returns ------------------------------------------------------------------
def run_with(qtbot, tmp_path, result, monkeypatch=None):
    plugin = Acts()
    plugin.result = result
    ws, manager, messages = workspace(qtbot, tmp_path, plugin)
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    return ws, manager, messages, plugin


def test_message_copy_and_nothing(qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warnings.append(a[2])))
    ws, _, messages, _ = run_with(qtbot, tmp_path, Message("all done"))
    assert messages == ["Acting plugin: all done"] and warnings == []
    ws, _, messages, _ = run_with(qtbot, tmp_path, Message("careful", "warning"))
    assert warnings == ["careful"]
    ws, _, _, _ = run_with(qtbot, tmp_path, Copy("hello clipboard"))
    assert QGuiApplication.clipboard().text() == "hello clipboard"
    ws, manager, _, _ = run_with(qtbot, tmp_path, None)  # returning nothing is fine
    assert manager.errors == {}


def test_several_results_are_all_carried_out_in_order(qtbot, tmp_path):
    ws, _, messages, _ = run_with(qtbot, tmp_path, [Message("one"), Copy("two"), Message("three")])
    assert [m for m in messages if m.startswith("Acting plugin: ") and "copied" not in m] == [
        "Acting plugin: one", "Acting plugin: three"]
    assert QGuiApplication.clipboard().text() == "two"


def test_open_url_only_opens_web_addresses(qtbot, tmp_path, monkeypatch):
    opened, warned = [], []
    monkeypatch.setattr(plugin_actions.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    run_with(qtbot, tmp_path, OpenUrl("https://console.aws.amazon.com/ec2/home?region=eu-west-1"))
    assert opened == ["https://console.aws.amazon.com/ec2/home?region=eu-west-1"] and warned == []
    run_with(qtbot, tmp_path, OpenUrl("file:///etc/passwd"))
    run_with(qtbot, tmp_path, OpenUrl("javascript:alert(1)"))
    assert len(opened) == 1 and len(warned) == 2 and "http(s)" in warned[0]


def test_show_text_opens_a_tab_in_the_activity_dock(qtbot, tmp_path):
    ws, _, _, _ = run_with(qtbot, tmp_path, ShowText("Instance details", "line one\nline two"))
    index = next(i for i in range(ws.log_tabs.count()) if ws.log_tabs.tabText(i) == "Instance details")
    assert ws.log_tabs.widget(index).toPlainText() == "line one\nline two"
    assert not ws.log_dock.isHidden()
    ws._close_log_tab(index)  # closing it works like any log tab
    assert all(ws.log_tabs.tabText(i) != "Instance details" for i in range(ws.log_tabs.count()))


def test_terminal_results_use_the_normal_terminal_launcher(qtbot, tmp_path, monkeypatch):
    plugin = Acts()
    ws, _, _ = workspace(qtbot, tmp_path, plugin)
    launched, warned = [], []
    ws._launch_terminal = lambda argv, message: launched.append((argv, message))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    plugin.result = Terminal(("ssh", "node-a"))
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    assert launched == [(["ssh", "node-a"], "Acting plugin: opened a terminal")]
    plugin.result = Terminal(())
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    plugin.result = Terminal(("ok", 5))
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    assert len(launched) == 1 and len(warned) == 2


def test_reload_reloads_only_open_lists_of_those_kinds(qtbot, tmp_path):
    ws, _, _, _ = None, None, None, None
    plugin = Acts()
    plugin.result = Reload(("Node",))
    ws, _, _ = workspace(qtbot, tmp_path, plugin)
    loaded = []
    ws.load_view = loaded.append
    ws.open_view("nodes")
    loaded.clear()
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    assert loaded == ["nodes"]  # not the pods list, which is not open and not a Node list
    plugin.result = Reload()
    ws.plugin_runner.run("acts", "copy", [node("n1")], "Node")
    assert loaded == ["nodes", "nodes"]  # no kinds: every open list


def test_a_failing_action_is_reported_recorded_and_contained(qtbot, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append((a[1], a[2]))))
    ws, manager, _, plugin = run_with(qtbot, tmp_path, RuntimeError("API said no"))
    assert warned and warned[0][0] == "Plugin Acting plugin" and "API said no" in warned[0][1]
    assert manager.errors == {"acts": "Copy name: API said no"}
    assert "failed: API said no" in ws.plugin_runner._output_tab.toPlainText()


def test_an_unknown_result_is_a_reported_problem(qtbot, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    run_with(qtbot, tmp_path, object())
    assert "does not understand" in warned[0]


def test_an_action_that_is_gone_is_reported(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    errors = []
    ws._on_error = errors.append
    ws.plugin_runner.run("acts", "no-such-action", [node("n1")], "Node")
    ws.plugin_runner.run("nobody", "copy", [node("n1")], "Node")
    assert len(errors) == 2


def test_closing_the_plugins_tab_and_logging_again_makes_a_new_one(qtbot, tmp_path):
    ws, _, _ = workspace(qtbot, tmp_path, Acts())
    ws.plugin_runner.note("first")
    index = next(i for i in range(ws.log_tabs.count()) if ws.log_tabs.tabText(i) == "Plugins")
    ws._close_log_tab(index)
    ws.plugin_runner.note("second")
    assert ws.plugin_runner._output_tab.toPlainText().endswith("second")


# ---- the dialog ---------------------------------------------------------------------------------
def test_the_dialog_says_what_a_plugin_adds_and_warns_about_dangerous_actions(qtbot, tmp_path):
    dialog = PluginsPanel(manager_with(tmp_path, Acts()))
    qtbot.addWidget(dialog)
    text = dialog.provides.text()
    assert "Right-click actions: Copy name (Node), ⚠ Kill (Node/Pod), Only in zone a (Node), Pod thing (Pod)" in text
    assert "always ask before they run" in text and "Columns" not in text
