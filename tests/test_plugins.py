import sys
import textwrap

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QMessageBox

from klotse.k8s.models import NodeRow
from klotse.plugins import Column, Plugin, Setting
from klotse.plugins.api import column_attr, valid_id
from klotse.plugins.host import RETRY_AFTER_S, PluginHost
from klotse.plugins.manager import PluginManager, _as_plugin
import klotse.ui.main_window as main_module
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.plugins_panel import PluginsPanel
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.terminal_settings import TerminalSettings
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


class Zone(Plugin):
    id = "zone"
    name = "Node zone"
    description = "Zone of every node"
    version = "1.2"
    columns = (Column("zone", "Zone"), Column("size", "Size", format=lambda v: f"{v} cores"))
    settings = (Setting("region", "Region", "eu-west-1"), Setting("live", "Use live data", True, kind="bool"))

    def __init__(self):
        self.requests = []

    def enrich(self, request):
        self.requests.append(request)
        return {r.key: {"zone": r.raw["metadata"]["labels"]["zone"], "size": 4} for r in request.resources}


def node(name, zone="a", uid=None):
    raw = {"metadata": {"name": name, "uid": uid or f"uid-{name}", "labels": {"zone": zone}}}
    return NodeRow(name, "Ready", "worker", "v1", "10.0.0.1", 4.0, 16.0, "1d", raw)


def manager_with(tmp_path, *plugins, enabled=True):
    manager = PluginManager(store(tmp_path), folder=None, bundled=(), entry_points=False)
    manager.loaded = [__import__("klotse.plugins.manager", fromlist=["Loaded"]).Loaded(p.id, "bundled", p.id, p) for p in plugins]
    if enabled:
        for plugin in plugins:
            manager.set_enabled(plugin.id, True)
    return manager


class Runner:
    """Stands in for the workspace's worker: collects the calls so a test decides when they finish."""

    def __init__(self):
        self.calls = []

    def __call__(self, fn, on_result, *args, on_error=None):
        self.calls.append((fn, on_result, on_error))

    def finish(self, index=-1):
        fn, on_result, on_error = self.calls.pop(index)
        try:
            result = fn()
        except Exception as error:
            on_error(str(error))
        else:
            on_result(result)


# ---- the API ------------------------------------------------------------------------
def test_ids_and_column_attributes():
    assert valid_id("aws-nodes_2") and not valid_id("AWS") and not valid_id("") and not valid_id("a b") and not valid_id(None)
    assert column_attr("zone", "size") == "plugin:zone:size"


def test_a_resource_key_is_its_name_or_namespace_slash_name():
    from klotse.plugins import ResourceInfo
    assert ResourceInfo("n1", "", {}).key == "n1" and ResourceInfo("p", "ns", {}).key == "ns/p"
    assert ResourceInfo("n1", "", {"metadata": {"uid": "u"}}).uid == "u" and ResourceInfo("n", "", {}).uid == ""


# ---- loading ------------------------------------------------------------------------
def test_a_plugin_file_in_the_folder_is_found(tmp_path):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "hello.py").write_text(textwrap.dedent('''
        from klotse.plugins import Column, Plugin

        class Hello(Plugin):
            id = "hello"
            name = "Hello"
            columns = (Column("greeting", "Greeting"),)

        plugin = Hello()
    '''))
    manager = PluginManager(store(tmp_path), folder=folder, bundled=(), entry_points=False)
    assert [(f.id, f.source, f.error) for f in manager.loaded] == [("hello", "plugins folder", "")]
    assert manager.get("hello").plugin.name == "Hello"
    assert not manager.is_enabled("hello") and manager.columns_for("Node") == []  # found, but off until enabled


def test_a_package_folder_and_a_register_function_work_too(tmp_path):
    folder = tmp_path / "plugins"
    (folder / "pkg").mkdir(parents=True)
    (folder / "pkg" / "__init__.py").write_text(textwrap.dedent('''
        from klotse.plugins import Plugin

        class P(Plugin):
            id = "from-package"

        def register():
            return P()
    '''))
    manager = PluginManager(store(tmp_path), folder=folder, bundled=(), entry_points=False)
    assert [f.id for f in manager.loaded] == ["from-package"]


def test_broken_plugins_are_reported_and_never_stop_the_others(tmp_path):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "a_good.py").write_text("from klotse.plugins import Plugin\nclass G(Plugin):\n    id = 'good'\nplugin = G()\n")
    (folder / "b_syntax.py").write_text("def broken(:\n")
    (folder / "c_empty.py").write_text("x = 1\n")
    (folder / "d_badid.py").write_text("from klotse.plugins import Plugin\nclass B(Plugin):\n    id = 'Bad Id'\nplugin = B()\n")
    (folder / "e_dup.py").write_text("from klotse.plugins import Plugin\nclass D(Plugin):\n    id = 'good'\nplugin = D()\n")
    (folder / "f_raises.py").write_text("raise RuntimeError('boom at import')\n")
    (folder / "_private.py").write_text("raise RuntimeError('must be skipped')\n")
    manager = PluginManager(store(tmp_path), folder=folder, bundled=(), entry_points=False)
    by_origin = {f.origin.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: f for f in manager.loaded}
    assert set(by_origin) == {"a_good.py", "b_syntax.py", "c_empty.py", "d_badid.py", "e_dup.py", "f_raises.py"}
    assert by_origin["a_good.py"].plugin is not None and by_origin["a_good.py"].error == ""
    assert "SyntaxError" in by_origin["b_syntax.py"].error
    assert "neither a `plugin` object" in by_origin["c_empty.py"].error
    assert "invalid plugin id" in by_origin["d_badid.py"].error
    assert "already uses the id" in by_origin["e_dup.py"].error
    assert "boom at import" in by_origin["f_raises.py"].error
    assert [p.id for p in [f.plugin for f in manager.loaded if f.plugin]] == ["good"]


def test_modules_and_entry_points_are_found_through_the_same_checks(tmp_path, monkeypatch):
    import types

    module = types.ModuleType("fake_bundled_plugin")
    module.plugin = Zone()
    monkeypatch.setitem(sys.modules, "fake_bundled_plugin", module)
    manager = PluginManager(store(tmp_path), folder=None, bundled=("fake_bundled_plugin", "no_such_module_anywhere"),
                            entry_points=False)
    assert [(f.id, f.source) for f in manager.loaded if f.plugin] == [("zone", "bundled")]
    assert "ModuleNotFoundError" in next(f for f in manager.loaded if f.plugin is None).error


def test_plugin_definitions_are_validated():
    class Dup(Plugin):
        id = "dup"
        columns = (Column("a", "A"), Column("a", "B"))

    class NoKinds(Plugin):
        id = "nokinds"
        columns = (Column("a", "A", kinds=()),)

    class NotAColumn(Plugin):
        id = "notacolumn"
        columns = ("x",)

    for bad in (Dup, NoKinds, NotAColumn, object(), 5):
        with pytest.raises((ValueError, TypeError)):
            _as_plugin(bad)
    assert isinstance(_as_plugin(Zone), Zone)  # a class is instantiated, an instance is used as it is


# ---- enabling and settings -----------------------------------------------------------
def test_enabling_is_saved_and_columns_follow(tmp_path):
    manager = manager_with(tmp_path, Zone(), enabled=False)
    changes = []
    manager.changed.connect(lambda: changes.append(1))
    assert manager.columns_for("Node") == [] and manager.enabled_for("Node") == []
    manager.set_enabled("zone", True)
    assert manager.is_enabled("zone") and changes == [1]
    titles = [c[0] for c in manager.columns_for("Node")]
    assert titles == ["Zone", "Size"] and manager.columns_for("Pod") == []  # only the kinds the plugin declared
    assert manager.columns_for("Node")[0] == ("Zone", "plugin:zone:zone")  # no formatter: a plain pair
    assert manager.columns_for("Node")[1][2](4) == "4 cores"
    again = PluginManager(store(tmp_path), folder=None, bundled=(), entry_points=False)
    assert again.is_enabled("zone")  # remembered
    manager.set_enabled("zone", False)
    assert manager.columns_for("Node") == [] and not PluginManager(store(tmp_path), entry_points=False).is_enabled("zone")


def test_settings_have_defaults_types_and_are_saved(tmp_path):
    manager = manager_with(tmp_path, Zone())
    assert manager.settings_for("zone") == {"region": "eu-west-1", "live": True}
    manager.set_setting("zone", "region", "us-east-2")
    manager.set_setting("zone", "live", False)
    assert manager.settings_for("zone") == {"region": "us-east-2", "live": False}
    assert PluginManager(store(tmp_path), bundled=(), entry_points=False).store.value("plugin_zone/region") == "us-east-2"


def test_failing_to_save_changes_nothing(tmp_path):
    manager = manager_with(tmp_path, Zone(), enabled=False)

    def broken():
        raise OSError("read-only")

    manager._sync = broken
    with pytest.raises(OSError):
        manager.set_enabled("zone", True)
    assert not manager.is_enabled("zone")


# ---- the host: asking plugins, caching ----------------------------------------------
def host_for(tmp_path, plugin=None):
    plugin = plugin or Zone()
    manager = manager_with(tmp_path, plugin)
    run = Runner()
    clock = [100.0]
    host = PluginHost(manager, "ctx", run, clock=lambda: clock[0])
    return host, plugin, run, manager, clock


def test_rows_are_enriched_off_the_gui_thread_and_cached(qtbot, tmp_path):
    host, plugin, run, _, _ = host_for(tmp_path)
    rows = [node("n1", "a"), node("n2", "b")]
    updated = []
    host.updated.connect(updated.append)
    host.rows_loaded("Node", rows)
    assert len(run.calls) == 1 and plugin.requests == []  # queued for a worker, not run on this thread
    assert [getattr(r, "extra", None) for r in rows] == [{}, {}]
    run.finish()
    assert [r.name for r in plugin.requests[0].resources] == ["n1", "n2"]
    assert plugin.requests[0].kind == "Node" and plugin.requests[0].context == "ctx"
    assert plugin.requests[0].settings == {"region": "eu-west-1", "live": True}
    assert updated == ["Node"]
    host.apply("Node", rows)
    assert rows[0].extra == {"plugin:zone:zone": "a", "plugin:zone:size": 4} and rows[1].extra["plugin:zone:zone"] == "b"


def test_known_resources_are_not_asked_again_but_new_ones_and_new_uids_are(qtbot, tmp_path):
    host, plugin, run, _, _ = host_for(tmp_path)
    host.rows_loaded("Node", [node("n1")])
    run.finish()
    reloaded = [node("n1"), node("n2")]  # the 10 s reload: n1 is cached, n2 is new
    host.rows_loaded("Node", reloaded)
    assert reloaded[0].extra["plugin:zone:zone"] == "a"  # cached values are on the new row objects at once
    run.finish()
    assert [r.name for r in plugin.requests[1].resources] == ["n2"]
    host.rows_loaded("Node", [node("n1"), node("n2")])
    assert run.calls == []  # everything known: no request at all
    host.rows_loaded("Node", [node("n1", uid="replacement")])  # same name, different node
    run.finish()
    assert [r.name for r in plugin.requests[2].resources] == ["n1"]


def test_values_older_than_refresh_s_are_fetched_again(qtbot, tmp_path):
    plugin = Zone()
    plugin.refresh_s = 300
    host, _, run, _, clock = host_for(tmp_path, plugin)
    host.rows_loaded("Node", [node("n1")])
    run.finish()
    clock[0] += 299
    host.rows_loaded("Node", [node("n1")])
    assert run.calls == []
    clock[0] += 2
    host.rows_loaded("Node", [node("n1")])
    assert len(run.calls) == 1


def test_one_request_at_a_time_per_plugin_and_kind(qtbot, tmp_path):
    host, _, run, _, _ = host_for(tmp_path)
    host.rows_loaded("Node", [node("n1")])
    host.rows_loaded("Node", [node("n1"), node("n2")])
    assert len(run.calls) == 1  # the first has not finished yet
    run.finish()
    host.rows_loaded("Node", [node("n1"), node("n2")])
    assert len(run.calls) == 1  # now n2 is asked for


def test_a_failing_plugin_is_reported_and_left_alone_for_a_while(qtbot, tmp_path):
    class Broken(Zone):
        id = "broken"

        def enrich(self, request):
            raise RuntimeError("API unreachable")

    host, _, run, manager, clock = host_for(tmp_path, Broken())
    host.rows_loaded("Node", [node("n1")])
    run.finish()
    assert manager.errors == {"broken": "API unreachable"}
    host.rows_loaded("Node", [node("n1")])
    assert run.calls == []  # backing off, not hammering a failing service every refresh
    clock[0] += RETRY_AFTER_S + 1
    host.rows_loaded("Node", [node("n1")])
    assert len(run.calls) == 1


def test_a_wrong_answer_is_a_reported_problem_and_unknown_columns_are_dropped(qtbot, tmp_path):
    class Odd(Zone):
        id = "odd"

        def enrich(self, request):
            return ["not", "a", "dict"]

    host, _, run, manager, _ = host_for(tmp_path, Odd())
    host.rows_loaded("Node", [node("n1")])
    run.finish()
    assert "expected a dict" in manager.errors["odd"]

    class Extra(Zone):
        id = "extra"

        def enrich(self, request):
            return {r.key: {"zone": "z", "not-declared": 1} for r in request.resources}

    host, _, run, manager, _ = host_for(tmp_path, Extra())
    rows = [node("n1")]
    host.rows_loaded("Node", rows)
    run.finish()
    host.apply("Node", rows)
    assert rows[0].extra == {"plugin:extra:zone": "z"} and manager.errors == {}


def test_a_success_clears_an_old_error(qtbot, tmp_path):
    host, _, run, manager, _ = host_for(tmp_path)
    manager.record_error("zone", "earlier problem")
    host.rows_loaded("Node", [node("n1")])
    run.finish()
    assert manager.errors == {}


def test_nothing_is_asked_when_no_plugin_has_columns_for_the_kind(qtbot, tmp_path):
    host, _, run, _, _ = host_for(tmp_path)
    rows = [node("p1")]
    host.rows_loaded("Pod", rows)
    assert run.calls == [] and rows[0].extra == {}


# ---- tables and the workspace --------------------------------------------------------
def test_plugin_columns_display_format_sort_and_tolerate_missing_values(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone())
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    table = ws.node_table
    assert [c[0] for c in table.model.columns][-2:] == ["Zone", "Size"]
    rows = [node("n1", "a"), node("n2", "b")]
    rows[0].extra = {"plugin:zone:size": 8, "plugin:zone:zone": "a"}
    table.model.set_rows(rows)
    zone_col = [c[1] for c in table.model.columns].index("plugin:zone:zone")
    size_col = [c[1] for c in table.model.columns].index("plugin:zone:size")
    assert table.model.index(0, size_col).data() == "8 cores" and table.model.index(1, size_col).data() == ""
    assert table.model.index(0, size_col).data(Qt.EditRole) == 8 and table.model.index(1, size_col).data(Qt.EditRole) is None
    assert table.model.index(0, zone_col).data() == "a" and table.model.headerData(zone_col, Qt.Horizontal) == "Zone"


def test_a_formatter_that_raises_never_breaks_the_table(qtbot, tmp_path):
    class Bad(Zone):
        id = "bad"
        columns = (Column("v", "V", format=lambda v: 1 / 0),)

    manager = manager_with(tmp_path, Bad())
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    row = node("n1")
    row.extra = {"plugin:bad:v": 5}
    ws.node_table.model.set_rows([row])
    col = [c[1] for c in ws.node_table.model.columns].index("plugin:bad:v")
    assert ws.node_table.model.index(0, col).data() == "5"


def test_end_to_end_rows_loaded_into_a_table_get_their_plugin_values(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone())
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.node_table.set_rows([node("n1", "us-east-1a"), node("n2", "us-east-1b")])
    assert len(run.calls) == 1
    run.finish()
    cols = [c[1] for c in ws.node_table.model.columns]
    zone = cols.index("plugin:zone:zone")
    assert [ws.node_table.model.index(r, zone).data() for r in range(2)] == ["us-east-1a", "us-east-1b"]


def test_enabling_and_disabling_updates_open_tables_at_once(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone(), enabled=False)
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    run = Runner()
    ws.plugin_host._run = run
    ws.node_table.set_rows([node("n1")])
    assert not any(c[1].startswith("plugin:") for c in ws.node_table.model.columns) and run.calls == []
    manager.set_enabled("zone", True)
    assert [c[0] for c in ws.node_table.model.columns][-2:] == ["Zone", "Size"]
    assert len(run.calls) == 1  # the rows already on screen are asked about straight away
    run.finish()
    assert ws.node_table.model.index(0, [c[1] for c in ws.node_table.model.columns].index("plugin:zone:zone")).data() == "a"
    manager.set_enabled("zone", False)
    assert not any(c[1].startswith("plugin:") for c in ws.node_table.model.columns)


def test_plugin_columns_join_the_saved_layout_and_the_columns_dialog(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone())
    settings = LabelSettings(store(tmp_path))
    settings.set_layout("Node", ["name", "plugin:zone:zone"], ["plugin:zone:size"])  # moved up, one hidden
    ws = ClusterWorkspace(None, "ctx", label_settings=settings, plugins=manager)
    qtbot.addWidget(ws)
    attrs = [c[1] for c in ws.node_table.model.columns]
    assert "plugin:zone:zone" in attrs and "plugin:zone:size" not in attrs  # the hidden one stays hidden
    assert attrs.index("plugin:zone:zone") < attrs.index("status") or attrs.index("plugin:zone:zone") < len(attrs) - 1
    assert "plugin:zone:size" in [c[1] for c in ws.node_table.base_columns]  # still offered in the Columns dialog


def test_tables_that_are_not_customizable_get_no_plugin_columns(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone())
    ws = ClusterWorkspace(None, "ctx", plugins=manager)
    qtbot.addWidget(ws)
    drilldown = ws.overview.pods_table
    assert not any(c[1].startswith("plugin:") for c in drilldown.model.columns)


def test_a_workspace_without_plugins_is_unchanged(qtbot):
    ws = ClusterWorkspace(None, "ctx")
    qtbot.addWidget(ws)
    assert ws.plugin_host is None and ws.node_table.extra_columns == []


# ---- the dialog and the window --------------------------------------------------------
def dialog_for(qtbot, manager):
    dialog = PluginsPanel(manager)
    qtbot.addWidget(dialog)
    return dialog


def test_the_dialog_lists_plugins_toggles_them_and_edits_settings(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone(), enabled=False)
    dialog = dialog_for(qtbot, manager)
    assert dialog.list.count() == 1 and dialog.list.item(0).text() == "Node zone"
    assert dialog.list.item(0).checkState() == Qt.Unchecked
    assert dialog.title.text() == "Node zone" and "Zone of every node" in dialog.description.text()
    assert "Version: 1.2" in dialog.source.text()
    dialog.list.item(0).setCheckState(Qt.Checked)
    assert manager.is_enabled("zone")
    assert dialog.settings_box.isVisibleTo(dialog) or dialog.settings_form.rowCount() == 2
    line = dialog.settings_form.itemAt(0, QFormLayout.ItemRole.FieldRole).widget()
    line.setText("ap-south-1")
    line.editingFinished.emit()
    assert manager.settings_for("zone")["region"] == "ap-south-1"
    box = dialog.settings_form.itemAt(1, QFormLayout.ItemRole.SpanningRole).widget()
    box.setChecked(False)
    assert manager.settings_for("zone")["live"] is False


def test_the_dialog_shows_why_a_plugin_failed_and_a_runtime_problem(qtbot, tmp_path):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "broken.py").write_text("raise RuntimeError('boom')\n")
    manager = PluginManager(store(tmp_path), folder=folder, bundled=(), entry_points=False)
    dialog = dialog_for(qtbot, manager)
    assert dialog.list.item(0).text().startswith("⚠") and "boom" in dialog.error.text()
    assert not dialog.list.item(0).flags() & Qt.ItemIsUserCheckable  # a failed plugin cannot be enabled
    working = manager_with(tmp_path, Zone())
    dialog = dialog_for(qtbot, working)
    assert dialog.error.text() == ""
    working.record_error("zone", "API unreachable")
    assert "API unreachable" in dialog.error.text()


def test_the_dialog_reverts_the_tick_when_saving_fails(qtbot, tmp_path, monkeypatch):
    manager = manager_with(tmp_path, Zone(), enabled=False)
    dialog = dialog_for(qtbot, manager)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    manager._sync = lambda: (_ for _ in ()).throw(OSError("read-only"))
    dialog.list.item(0).setCheckState(Qt.Checked)
    assert warned == ["read-only"] and dialog.list.item(0).checkState() == Qt.Unchecked and not manager.is_enabled("zone")


def test_reloading_finds_a_plugin_added_to_the_folder(qtbot, tmp_path):
    folder = tmp_path / "plugins"
    folder.mkdir()
    manager = PluginManager(store(tmp_path), folder=folder, bundled=(), entry_points=False)
    dialog = dialog_for(qtbot, manager)
    assert dialog.list.count() == 0 and "No plugins found" in dialog.empty.text()
    (folder / "late.py").write_text("from klotse.plugins import Plugin\nclass L(Plugin):\n    id = 'late'\nplugin = L()\n")
    dialog.reload()
    assert dialog.list.count() == 1 and dialog.empty.text() == ""


def test_plugins_live_in_preferences_not_in_a_menu_of_their_own(qtbot, tmp_path, monkeypatch):
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    # no Settings menu anywhere; "Plugins" only appears while an enabled plugin has settings for each cluster
    assert [a.text() for a in win.menuBar().actions() if a.isVisible()] == ["File", "Edit", "View", "Help"]
    assert "Plugins…" not in [a.text() for a in win.edit_menu.actions()]
    assert win.plugins.folder == tmp_path / "plugins"
    win.open_cluster("ctx")
    assert win.workspace("ctx").plugins is win.plugins
    seen = {}

    class FakeDialog:
        Accepted = 1

        def __init__(self, kubeconfig, folder, parent=None, terminal=None, plugins=None):
            seen.update(terminal=terminal, plugins=plugins)

        def exec(self):
            return 0

        def chosen_kubeconfig(self):
            return None

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "PreferencesDialog", FakeDialog)
    monkeypatch.setattr(main_module.sys, "platform", "linux")
    win.open_preferences()
    assert seen == {"terminal": None, "plugins": win.plugins}  # the terminal choice exists on macOS only
    monkeypatch.setattr(main_module.sys, "platform", "darwin")
    win.open_preferences()
    assert seen == {"terminal": win.terminal_settings, "plugins": win.plugins}


def test_preferences_has_a_page_per_thing_to_configure(qtbot, tmp_path):
    kubeconfig = KubeconfigSettings(store(tmp_path))
    plain = PreferencesDialog(kubeconfig, tmp_path / "cfg")
    qtbot.addWidget(plain)
    assert [plain.tabs.tabText(i) for i in range(plain.tabs.count())] == ["General"] and plain.tabs.tabBar().isHidden()
    manager = manager_with(tmp_path, Zone(), enabled=False)
    terminal = TerminalSettings(store(tmp_path))
    full = PreferencesDialog(kubeconfig, tmp_path / "cfg", terminal=terminal, plugins=manager)
    qtbot.addWidget(full)
    assert [full.tabs.tabText(i) for i in range(full.tabs.count())] == ["General", "Terminal", "Plugins"]
    assert not full.tabs.tabBar().isHidden()
    only_plugins = PreferencesDialog(kubeconfig, tmp_path / "cfg", plugins=manager)
    qtbot.addWidget(only_plugins)
    assert [only_plugins.tabs.tabText(i) for i in range(only_plugins.tabs.count())] == ["General", "Plugins"]


def test_the_plugins_page_applies_at_once_while_saving_keeps_the_other_pages(qtbot, tmp_path):
    manager = manager_with(tmp_path, Zone(), enabled=False)
    terminal = TerminalSettings(store(tmp_path))
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), tmp_path / "cfg", terminal=terminal, plugins=manager)
    qtbot.addWidget(dialog)
    dialog.plugins_panel.list.item(0).setCheckState(Qt.Checked)
    assert manager.is_enabled("zone")  # no need to press Save: Cancel afterwards would not undo it
    dialog.terminal_panel.terminal.setCurrentText("iTerm2")
    assert terminal.mac_terminal == "Terminal"  # a choice on the other pages waits for Save
    dialog._save()
    assert terminal.mac_terminal == "iTerm2" and dialog.result() == QDialog.Accepted


def test_an_answer_with_warnings_is_asked_for_again_after_a_minute_not_after_refresh_s(qtbot, tmp_path):
    class Partial(Zone):
        id = "partial"
        refresh_s = 1800

        def enrich(self, request):
            if request.plugin_id == "partial" and self.warn:
                request.warn("the login expired")
            return {r.key: {"zone": "a"} for r in request.resources}

    plugin = Partial()
    plugin.warn = True
    host, _, run, manager, clock = host_for(tmp_path, plugin)
    rows = [node("n1")]
    host.rows_loaded("Node", rows)
    run.finish()
    assert manager.errors == {"partial": "the login expired"}
    host.apply("Node", rows)
    assert rows[0].extra["plugin:partial:zone"] == "a"  # the values are used even so
    clock[0] += 30
    host.rows_loaded("Node", [node("n1")])
    assert run.calls == []  # too soon
    clock[0] += RETRY_AFTER_S
    plugin.warn = False  # the user signed in again
    host.rows_loaded("Node", [node("n1")])
    assert len(run.calls) == 1  # asked again long before the 30 minutes
    run.finish()
    assert manager.errors == {}
    clock[0] += 1000
    host.rows_loaded("Node", [node("n1")])
    assert run.calls == []  # a complete answer waits for refresh_s again
    clock[0] += 900
    host.rows_loaded("Node", [node("n1")])
    assert len(run.calls) == 1
