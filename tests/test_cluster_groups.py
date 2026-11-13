from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox

from klotse.ui.cluster_groups import ClusterGroups, clean_path, inside, lineage, parent_path
from klotse.ui.customizations import LabelSettings
from klotse.ui.group_suggest import SuggestGroupsDialog, propose, tokens
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator
from test_app import FakeService, MultiService

NAMES = ["group-of-cluster.api.prod.us1", "group-of-cluster.api.prod.us2", "group-of-cluster.api.stg.us1",
         "group-of-cluster.web.prod.eu1", "lab", "other.thing"]


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


# ---- paths and the saved model ------------------------------------------------
def test_paths_are_cleaned_and_split():
    assert clean_path("  prod // eu /  ") == "prod/eu" and clean_path(None) == ""
    assert parent_path("a/b/c") == "a/b" and parent_path("a") == ""
    assert lineage("a/b/c") == ["a", "a/b", "a/b/c"] and lineage("") == []
    assert inside("a/b", "a") and inside("a", "a") and not inside("ab", "a")


def test_groups_round_trip_through_the_settings_file(tmp_path):
    groups = ClusterGroups(store(tmp_path))
    groups.assign("arn:aws:eks:eu:1:cluster/prod", "prod/eu")
    groups.assign("dev", "lab")
    groups.add_group("prod/asia")
    groups.save()
    again = ClusterGroups(store(tmp_path))
    assert again.assignments == {"arn:aws:eks:eu:1:cluster/prod": "prod/eu", "dev": "lab"}
    assert again.empty == ["prod/asia"]
    assert again.groups() == ["lab", "prod", "prod/asia", "prod/eu"]  # parents of nested groups are included


def test_nothing_is_written_until_save(tmp_path):
    groups = ClusterGroups(store(tmp_path))
    groups.assign("a", "g")
    assert not (tmp_path / "klotse.ini").exists() and ClusterGroups(store(tmp_path)).assignments == {}


def test_rename_moves_a_group_with_everything_below_it():
    groups = ClusterGroups(QSettings())
    groups.assignments = {"a": "prod", "b": "prod/eu", "c": "production"}
    groups.empty = ["prod/asia"]
    assert groups.rename_group("prod", "live") == "live"
    assert groups.assignments == {"a": "live", "b": "live/eu", "c": "production"}  # "production" is another group
    assert groups.empty == ["live/asia"]


def test_deleting_a_group_keeps_its_clusters_one_level_up():
    groups = ClusterGroups(QSettings())
    groups.assignments = {"a": "prod", "b": "prod/eu", "c": "prod/eu/x", "d": "lab"}
    groups.empty = ["prod/asia"]
    groups.delete_group("prod/eu")
    assert groups.assignments == {"a": "prod", "b": "prod", "c": "prod", "d": "lab"}
    groups.delete_group("prod")
    assert groups.assignments == {"d": "lab"} and groups.empty == []  # top level: clusters become ungrouped


# ---- suggestions ----------------------------------------------------------------
def test_names_are_split_at_separators_and_trailing_numbers():
    assert tokens("group-of-cluster.api.prod.us1") == ["group-of-cluster", "api", "prod", "us", "1"]
    assert tokens("group-of-cluster.api.prod.us1", split_numbers=False)[-1] == "us1"
    assert tokens("a-b.c", separators="./:_@-") == ["a", "b", "c"]
    assert tokens("12") == ["12"] and tokens("cluster/name") == ["cluster", "name"]


def test_shared_prefixes_become_nested_groups_the_way_the_example_describes():
    chains = propose(NAMES)
    assert chains["group-of-cluster.api.prod.us1"] == [
        "group-of-cluster", "group-of-cluster/api", "group-of-cluster/api/prod", "group-of-cluster/api/prod/us"]
    assert chains["group-of-cluster.api.prod.us2"][-1] == "group-of-cluster/api/prod/us"
    assert chains["group-of-cluster.api.stg.us1"] == ["group-of-cluster", "group-of-cluster/api"]  # stg is alone
    assert chains["group-of-cluster.web.prod.eu1"] == ["group-of-cluster"]
    assert "lab" not in chains and "other.thing" not in chains  # nothing shared, or too few clusters


def test_minimum_size_and_depth_limit_the_suggestions():
    assert "group-of-cluster/api/prod" not in sum(propose(NAMES, depth=2).values(), [])
    assert propose(NAMES, min_size=4)["group-of-cluster.api.prod.us1"] == ["group-of-cluster"]
    assert propose(["a-b-1", "a-b-2"], split_numbers=False) == {}  # "-" is not a separator by default
    assert propose(["a-b-1", "a-b-2"]) == {"a-b-1": ["a-b"], "a-b-2": ["a-b"]}  # but the shared "a-b" before the number is
    assert propose(["a-b-1", "a-b-2"], separators="./:_@-")["a-b-1"] == ["a", "a/b"]


def test_a_slash_inside_a_name_never_adds_a_level_by_accident():
    chains = propose(["arn:x/prod1", "arn:x/prod2"], separators=":")
    assert chains["arn:x/prod1"] == ["arn", "arn/x-prod"]


def test_the_dialog_assigns_each_cluster_to_its_deepest_ticked_group(qtbot):
    dialog = SuggestGroupsDialog(NAMES, ClusterGroups(QSettings()))
    qtbot.addWidget(dialog)
    assigned = dialog.assignments()
    assert assigned["group-of-cluster.api.prod.us1"] == "group-of-cluster/api/prod/us"
    assert assigned["group-of-cluster.api.stg.us1"] == "group-of-cluster/api"
    assert "lab" not in assigned
    for i in range(dialog.found.count()):  # untick the deepest levels: clusters fall back to the parent
        if dialog.found.item(i).data(Qt.UserRole).count("/") >= 2:
            dialog.found.item(i).setCheckState(Qt.Unchecked)
    assert dialog.assignments()["group-of-cluster.api.prod.us1"] == "group-of-cluster/api"
    assert dialog.apply_button.isEnabled()


def test_the_dialog_leaves_clusters_that_already_have_a_group_unless_asked(qtbot):
    groups = ClusterGroups(QSettings())
    groups.assign("group-of-cluster.api.prod.us1", "mine")
    dialog = SuggestGroupsDialog(NAMES, groups)
    qtbot.addWidget(dialog)
    assert "group-of-cluster.api.prod.us1" not in dialog.assignments()
    dialog.regroup.setChecked(True)
    assert "group-of-cluster.api.prod.us1" in dialog.assignments()


# ---- the sidebar ---------------------------------------------------------------
def make_nav(qtbot, names=("a", "b", "c", "d"), assignments=None, empty=()):
    groups = ClusterGroups(QSettings())
    groups.assignments = dict(assignments or {})
    groups.empty = list(empty)
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(list(names), groups)
    return nav, groups


def texts(parent):
    return [parent.child(i).data(0, Navigator.ROLE_KEY)
            if parent.child(i).data(0, Navigator.ROLE_KIND) == "cluster"
            else parent.child(i).text(0)
            for i in range(parent.childCount())]


def test_groups_are_folders_in_the_disconnected_section(qtbot):
    nav, _ = make_nav(qtbot, assignments={"a": "prod", "b": "prod/eu", "c": "prod/eu"})
    idle = nav.topLevelItem(1)
    assert idle.text(0) == "DISCONNECTED (4)"
    assert texts(idle) == ["prod (3)", "d"]  # folders first, then clusters without a group
    prod = idle.child(0)
    assert texts(prod) == ["eu (2)", "a"]
    assert texts(prod.child(0)) == ["b", "c"]
    assert nav.cluster_item("b").toolTip(0) == "Status: Disconnected\nGroup: prod/eu"


def test_a_connected_cluster_leaves_its_folder_and_returns_on_disconnect(qtbot):
    nav, _ = make_nav(qtbot, assignments={"a": "prod", "b": "prod"})
    nav.set_state("a", "connected")
    connected, idle = nav.topLevelItem(0), nav.topLevelItem(1)
    assert texts(connected) == ["a"] and connected.text(0) == "CONNECTED (1)"
    assert texts(idle.child(0)) == ["b"] and idle.child(0).text(0) == "prod (1)"
    nav.set_state("a", "idle")
    assert texts(idle.child(0)) == ["a", "b"] and idle.child(0).text(0) == "prod (2)"


def test_create_rename_move_and_delete_update_the_tree_and_tell_the_owner(qtbot):
    nav, groups = make_nav(qtbot)
    changes = []
    nav.groups_changed.connect(lambda: changes.append(1))
    assert nav.create_group("", "prod") == "prod" and nav.create_group("prod", "eu") == "prod/eu"
    nav.move_cluster("a", "prod/eu")
    idle = nav.topLevelItem(1)
    assert texts(idle) == ["prod (1)", "b", "c", "d"] and texts(idle.child(0)) == ["eu (1)"]
    assert nav.rename_group("prod", "live") == "live"
    assert groups.group_of("a") == "live/eu" and texts(idle)[0] == "live (1)"
    assert nav.delete_group("live/eu", confirm=False) and groups.group_of("a") == "live"
    assert nav.delete_group("live", confirm=False) and groups.group_of("a") == "" and texts(idle) == ["a", "b", "c", "d"]
    assert len(changes) == 6


def test_asking_for_names_and_confirming_a_delete_use_dialogs(qtbot, monkeypatch):
    nav, groups = make_nav(qtbot)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("  team / x ", True)))
    assert nav.create_group("") == "team/x"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    assert nav.create_group("") == ""  # cancelled
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert nav.delete_group("team/x") is False and "team/x" in groups.groups()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert nav.delete_group("team/x") is True


def test_connect_all_asks_for_every_idle_cluster_in_the_group_and_below(qtbot):
    nav, _ = make_nav(qtbot, assignments={"a": "prod", "b": "prod/eu", "c": "prod/eu", "d": "other"})
    nav.set_state("c", "connected")
    asked = []
    nav.connect_requested.connect(asked.append)
    assert nav.connect_all("prod") == ["a", "b"] and asked == ["a", "b"]


def test_context_menus_offer_the_group_actions(qtbot):
    nav, _ = make_nav(qtbot, assignments={"a": "prod"})
    idle = nav.topLevelItem(1)
    labels = lambda menu: [a.text() for a in menu.actions() if a.text()]  # noqa: E731
    collapse_expand = ["Collapse clusters (show only the cluster list)", "Expand clusters"]
    assert labels(nav.menu_for(idle)) == collapse_expand + ["New group…", "Suggest groups…"]
    assert labels(nav.menu_for(idle.child(0))) == ["Connect all", "New subgroup…", "Rename…", "Delete group"]
    cluster_menu = nav.menu_for(nav.cluster_item("a"))
    assert labels(cluster_menu) == ["Connect", "Rename alias…", "Sign in with the helper…", "Show kubeconfig entry…", "Move to group"]
    move = next(a for a in cluster_menu.actions() if a.text() == "Move to group").menu()
    assert labels(move) == ["prod", "New group…", "No group"]
    assert labels(nav.menu_for(nav.topLevelItem(0))) == [  # the CONNECTED header: auto-refresh for all clusters, then fold or open
        "Turn auto-refresh off on all connected clusters", "Turn auto-refresh on on all connected clusters",
        "Restore each cluster's previous auto-refresh"] + collapse_expand


def test_without_groups_the_list_is_the_plain_two_sections(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a", "b"])
    assert [nav.topLevelItem(1).child(i).data(0, nav.ROLE_KEY)
            for i in range(nav.topLevelItem(1).childCount())] == ["a", "b"] and nav.groups is None
    labels = [a.text() for a in nav.menu_for(nav.cluster_item("a")).actions() if a.text()]
    assert labels == ["Connect", "Rename alias…", "Sign in with the helper…", "Show kubeconfig entry…"]
    header = [a.text() for a in nav.menu_for(nav.topLevelItem(1)).actions() if a.text()]
    assert header == ["Collapse clusters (show only the cluster list)", "Expand clusters"]  # no groups: only collapse and expand


def test_search_filters_both_sections_by_context_or_alias_without_changing_groups(qtbot):
    nav, groups = make_nav(qtbot, assignments={"a": "prod/eu", "b": "prod/us"})
    nav.set_alias("a", "Prod Europe")
    nav.set_state("c", "connected")

    nav.set_filter("prod eur")
    assert not nav.cluster_item("a").isHidden()
    assert not nav.cluster_item("a").parent().isHidden()
    assert not nav.cluster_item("a").parent().parent().isHidden()
    assert nav.cluster_item("b").isHidden() and nav.cluster_item("c").isHidden()
    assert not nav.topLevelItem(1).isHidden() and nav.topLevelItem(0).isHidden()

    nav.set_filter("c")  # the disconnected context is searchable alongside connected clusters
    assert not nav.cluster_item("c").isHidden() and not nav.topLevelItem(0).isHidden()
    assert nav.cluster_item("a").isHidden()

    nav.set_filter("")
    assert all(not nav.cluster_item(name).isHidden() for name in ("a", "b", "c", "d"))
    assert groups.assignments == {"a": "prod/eu", "b": "prod/us"}


# ---- the window saves them --------------------------------------------------------
def test_the_window_saves_group_changes_and_restores_them(qtbot, tmp_path):
    win = MainWindow(MultiService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    win.nav.create_group("", "prod")
    win.nav.move_cluster("a", "prod")
    again = MainWindow(MultiService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(again)
    assert again.groups.group_of("a") == "prod"
    assert texts(again.nav.topLevelItem(1))[0] == "prod (1)"
    again.nav.set_state("a", "connected")
    again.nav.set_state("a", "idle")
    assert again.nav.cluster_item("a").parent() is again.nav._group_items["prod"]


def test_a_settings_file_that_cannot_be_written_warns_and_keeps_the_groups(qtbot, tmp_path, monkeypatch):
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))

    def broken():
        raise OSError("read-only")

    monkeypatch.setattr(win.groups, "save", broken)
    win.nav.create_group("", "prod")
    assert warned == ["read-only"] and "prod" in win.groups.groups()


def test_the_cluster_menu_belongs_to_the_cluster_row_not_to_the_views_under_it(qtbot):
    from PySide6.QtWidgets import QTreeWidgetItem
    nav, _ = make_nav(qtbot, assignments={"a": "prod"})
    cluster = nav.cluster_item("a")
    view = QTreeWidgetItem(cluster)
    view.setData(0, nav.ROLE_KIND, "view")
    view.setData(0, nav.ROLE_KEY, "overview")
    assert nav.menu_for(view) is None  # Overview, Nodes and the other views under a cluster offer no cluster menu
    assert nav.menu_for(cluster) is not None
