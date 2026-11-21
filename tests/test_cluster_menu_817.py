"""#817: a cluster's menu in the cluster list comes in four groups (connection; alias and group; the cluster's settings;
its kubeconfig entry), with one separator between groups that have entries and none at the ends."""
from PySide6.QtCore import QSettings

from klotse.ui.cluster_groups import ClusterGroups
from klotse.ui.navigator import Navigator


def layout(menu):
    """The menu's entries, a separator shown as "---"."""
    return ["---" if a.isSeparator() else a.text() for a in menu.actions()]


def navigator(qtbot, tmp_path=None):
    nav = Navigator()
    qtbot.addWidget(nav)
    groups = ClusterGroups(QSettings(str(tmp_path / "g.ini"), QSettings.IniFormat)) if tmp_path else None
    nav.set_contexts(["prod", "lab"], groups)
    nav.plugin_entries = lambda: [("aws", "AWS Plugin: settings for this cluster…")]
    nav.auto_refresh_is_on = lambda context: True
    nav.removable = lambda context: context == "lab"
    return nav


def test_connected_cluster_menu_groups(qtbot, tmp_path):
    nav = navigator(qtbot, tmp_path)
    nav.set_state("prod", "connected")
    assert layout(nav.menu_for(nav._clusters["prod"])) == [
        "Reconnect", "Disconnect", "---",
        "Rename alias…", "Move to group", "---",
        "AWS Plugin: settings for this cluster…", "Metrics source…", "Turn off auto-refresh", "---",
        "Show kubeconfig entry…"]


def test_disconnected_cluster_menu_has_no_settings_group_and_ends_with_its_kubeconfig_entry(qtbot, tmp_path):
    nav = navigator(qtbot, tmp_path)
    assert layout(nav.menu_for(nav._clusters["lab"])) == [
        "Connect", "---",
        "Rename alias…", "Move to group", "---",
        "Show kubeconfig entry…", "Sign in with the helper…", "Remove from Klotse…"]


def test_without_groups_or_plugins_there_are_no_empty_groups_or_doubled_separators(qtbot):
    nav = navigator(qtbot)
    nav.plugin_entries = lambda: []
    nav.set_state("prod", "connected")
    for name in ("prod", "lab"):
        entries = layout(nav.menu_for(nav._clusters[name]))
        assert entries[0] != "---" and entries[-1] != "---"
        assert all(not (a == b == "---") for a, b in zip(entries, entries[1:]))
    assert layout(nav.menu_for(nav._clusters["prod"])) == [
        "Reconnect", "Disconnect", "---", "Rename alias…", "---", "Metrics source…", "Turn off auto-refresh", "---",
        "Show kubeconfig entry…"]
