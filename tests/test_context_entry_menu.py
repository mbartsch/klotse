"""Right-clicking a cluster offers its kubeconfig entry, connected or not; the window shows it read-only and never connects."""
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtCore import QSettings

from klotse.ui import context_dialog
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator
from test_app import FakeService


def test_the_cluster_menu_offers_the_entry_for_a_disconnected_cluster(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod/eu"])
    menu = nav.menu_for(nav.cluster_item("prod/eu"))
    assert "Show kubeconfig entry…" in [a.text() for a in menu.actions()]
    seen = []
    nav.config_requested.connect(seen.append)
    next(a for a in menu.actions() if a.text() == "Show kubeconfig entry…").trigger()
    assert seen == ["prod/eu"]


def test_the_window_opens_the_entry_dialog_without_connecting(qtbot, tmp_path, monkeypatch):
    kube = tmp_path / "config"
    kube.write_text("contexts:\n- name: prod/eu\n  context: {cluster: c, user: u}\nclusters:\n- name: c\n  cluster: {server: 'https://eu.test'}\n"
                    "users:\n- name: u\n  user: {token: DO-NOT-SHOW}\n", encoding="utf-8")
    shown = {}

    def fake_exec(self):
        shown["text"] = self.findChild(QPlainTextEdit).toPlainText()
        return 0

    monkeypatch.setattr(context_dialog.ContextEntryDialog, "exec", fake_exec)
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    win.kubeconfig_settings.path = str(kube)
    win.show_context_entry("prod/eu")
    assert "Server: https://eu.test" in shown["text"] and "bearer token: present (not shown)" in shown["text"]
    assert "DO-NOT-SHOW" not in shown["text"]
    assert win.workspaces == {}  # nothing was connected
