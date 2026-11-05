from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.plugins import Plugin, Setting
from klotse.ui.cluster_settings_dialog import ClusterSettingsDialog
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse_plugins.aws import AwsPlugin, console
from test_app import FakeService, open_active
from test_plugins import manager_with, store

ARN = "arn:aws:eks:eu-west-1:123456789012:cluster/prod"


class Zoned(Plugin):
    id = "zoned"
    name = "Zoned"
    settings = (Setting("region", "Region", "eu-west-1"),)
    cluster_settings = (Setting("team", "Team"), Setting("acct", "Account", pattern=r"\d{12}", help="12 digits"))


def test_cluster_settings_are_saved_per_cluster_and_reach_that_cluster_only(tmp_path):
    manager = manager_with(tmp_path, Zoned())
    seen = []
    manager.settings_changed.connect(seen.append)
    manager.set_cluster_settings("zoned", "prod-eu", {"team": "blue", "acct": ""})
    manager.set_cluster_settings("zoned", ARN, {"team": "red, with comma"})
    assert seen == ["zoned", "zoned"]
    assert manager.cluster_settings_for("zoned", "prod-eu") == {"team": "blue"}  # empty values are not kept
    assert manager.settings_for("zoned", "prod-eu") == {"region": "eu-west-1", "team": "blue", "acct": ""}
    assert manager.settings_for("zoned", ARN)["team"] == "red, with comma"
    assert manager.settings_for("zoned", "other") == {"region": "eu-west-1", "team": "", "acct": ""}
    assert manager.settings_for("zoned") == {"region": "eu-west-1"}  # without a cluster: the general settings only
    again = manager_with(tmp_path, Zoned())  # survives a restart (same file)
    assert again.cluster_settings_for("zoned", "prod-eu") == {"team": "blue"}
    manager.set_cluster_settings("zoned", "prod-eu", {"team": ""})  # clearing everything forgets the cluster
    assert manager.cluster_settings_for("zoned", "prod-eu") == {} and manager.cluster_settings_for("zoned", ARN)


def test_aws_profile_and_account_follow_the_cluster_settings():
    settings = {"aws_profile": "default-one", "profile_map": "prod=mapped", "account_map": "prod=111111111111"}
    assert console.profile_for("prod", settings) == "mapped"
    assert console.profile_for("prod", {**settings, "cluster_profile": "menu"}) == "menu"  # the menu wins
    assert console.profile_for("other", {**settings, "cluster_profile": " "}) == "default-one"
    assert console.account_for("prod", settings) == "111111111111"
    assert console.account_for("prod", {**settings, "cluster_account": "222222222222"}) == "222222222222"
    assert console.account_for(ARN, {"cluster_account": "not-an-account"}) == "123456789012"  # bad value is ignored


def test_the_aws_plugin_declares_a_profile_and_an_account_per_cluster():
    assert [s.key for s in AwsPlugin.cluster_settings] == ["cluster_profile", "cluster_account"]


def test_the_dialog_saves_validates_and_cancels(qtbot, tmp_path, monkeypatch):
    plugin = Zoned()
    manager = manager_with(tmp_path, plugin)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    dialog = ClusterSettingsDialog(manager, plugin, "prod")
    qtbot.addWidget(dialog)
    dialog.fields["acct"].setText("12")
    dialog.save()
    assert warnings and dialog.result() != QDialog.Accepted and manager.cluster_settings_for("zoned", "prod") == {}
    dialog.fields["acct"].setText("123456789012")
    dialog.fields["team"].setText(" blue ")
    dialog.save()
    assert dialog.result() == QDialog.Accepted
    assert manager.cluster_settings_for("zoned", "prod") == {"team": "blue", "acct": "123456789012"}
    reopened = ClusterSettingsDialog(manager, plugin, "prod")
    qtbot.addWidget(reopened)
    assert reopened.values() == {"team": "blue", "acct": "123456789012"}


def test_the_plugins_menu_needs_an_enabled_plugin_and_a_cluster_in_front(qtbot, tmp_path, monkeypatch):
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    win.plugins.loaded = manager_with(tmp_path, Zoned(), enabled=False).loaded
    win.plugins.set_enabled("zoned", False)
    assert not win.plugins_menu.menuAction().isVisible()
    win.plugins.set_enabled("zoned", True)
    assert win.plugins_menu.menuAction().isVisible()
    win._fill_plugins_menu()
    entries = [a for a in win.plugins_menu.actions() if not a.isSeparator()]
    assert entries[0].text() == "Zoned: settings for this cluster…" and not entries[0].isEnabled()  # no cluster yet
    assert win.open_cluster_settings("zoned") is False
    open_active(win)
    win._fill_plugins_menu()
    assert win.plugins_menu.actions()[0].isEnabled()
    shown = {}

    def fake_exec(self):
        shown.update(context=self.context, plugin=self.plugin.id)
        self.fields["team"].setText("blue")
        self.save()
        return self.result()

    monkeypatch.setattr(ClusterSettingsDialog, "exec", fake_exec)
    assert win.open_cluster_settings("zoned") is True
    assert shown["plugin"] == "zoned" and win.plugins.cluster_settings_for("zoned", shown["context"]) == {"team": "blue"}
    win.plugins.set_enabled("zoned", False)
    assert win.open_cluster_settings("zoned") is False and not win.plugins_menu.menuAction().isVisible()


def test_a_connected_cluster_offers_the_plugin_settings_on_right_click(qtbot, tmp_path, monkeypatch):
    win = MainWindow(FakeService, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    win.plugins.loaded = manager_with(tmp_path, Zoned(), enabled=False).loaded
    ctx = win.contexts[0]
    texts = lambda: [a.text() for a in win.nav.menu_for(win.nav._clusters[ctx]).actions()]  # noqa: E731
    win.plugins.set_enabled("zoned", True)
    assert "Zoned: settings for this cluster…" not in texts()  # not connected yet
    open_active(win)
    assert "Zoned: settings for this cluster…" in texts()
    opened = []
    monkeypatch.setattr(ClusterSettingsDialog, "exec", lambda self: opened.append(self.context) or 0)
    next(a for a in win.nav.menu_for(win.nav._clusters[ctx]).actions() if a.text().startswith("Zoned")).trigger()
    assert opened == [win.clusters.currentWidget().context]
    win.plugins.set_enabled("zoned", False)
    assert "Zoned: settings for this cluster…" not in texts()
