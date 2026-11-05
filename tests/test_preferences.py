from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox, QPlainTextEdit

from klotse.k8s import client as client_module
from klotse.k8s.client import KubeService
from klotse.k8s.models import PodRow
from klotse.ui import edit_menu, main_window as main_module, preferences as preferences_module
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.node_dialogs import DrainDialog
from klotse.ui.overview import POD_COLUMNS_BASIC
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from klotse.ui.resource_table import ResourceTable
from klotse.ui.terminal import kubectl_args, node_shell_args

from test_app import FakeService, open_active


def store(tmp_path):
    return QSettings(str(tmp_path / "nested" / "Klotse.ini"), QSettings.IniFormat)


def window(qtbot, tmp_path):
    win = MainWindow(FakeService, LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    open_active(win)
    return win


def kubeconfig_file(tmp_path, name="other-config"):
    path = tmp_path / name
    path.write_text("apiVersion: v1\nkind: Config\n")
    return str(path)


# ---- Edit menu ---------------------------------------------------------------------------------
def test_edit_menu_has_the_traditional_commands_and_preferences(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    assert [a.text() for a in win.menuBar().actions()][:3] == ["File", "Edit", "View"]
    texts = [a.text() for a in win.edit_menu.actions() if not a.isSeparator()]
    assert texts == ["Undo", "Redo", "Cut", "Copy", "Paste", "Select All", "Preferences…"]
    assert win.edit_actions.actions["copy"].shortcut().toString() and win.preferences_action.menuRole() is not None


def test_text_box_commands_cut_copy_paste_undo(qtbot):
    box = QLineEdit("hello")
    qtbot.addWidget(box)
    caps = edit_menu.capabilities(box)
    assert caps["select_all"] and not caps["copy"] and not caps["cut"]
    edit_menu.run("select_all", box)
    edit_menu.run("copy", box)
    assert QGuiApplication.clipboard().text() == "hello"
    edit_menu.run("cut", box)
    assert box.text() == ""
    edit_menu.run("paste", box)
    assert box.text() == "hello"
    edit_menu.run("undo", box)
    assert box.text() == ""
    edit_menu.run("redo", box)
    assert box.text() == "hello"


def test_read_only_text_can_copy_but_not_cut_paste_or_undo(qtbot):
    pane = QPlainTextEdit("details", readOnly=True)
    qtbot.addWidget(pane)
    pane.selectAll()
    caps = edit_menu.capabilities(pane)
    assert caps["copy"] and caps["select_all"] and not (caps["cut"] or caps["paste"] or caps["undo"])
    edit_menu.run("cut", pane)
    assert pane.toPlainText() == "details"


def test_table_copy_gives_selected_rows_as_tab_separated_text(qtbot):
    table = ResourceTable(POD_COLUMNS_BASIC, "Pod", customizable=False)
    qtbot.addWidget(table)
    table.model.set_rows([PodRow("a", "ns", "1/1", "Running", 0, "n1", "1d"),
                          PodRow("b", "ns", "0/1", "Pending", 2, "n2", "2d")])
    assert not edit_menu.capabilities(table.view)["copy"]
    edit_menu.run("select_all", table.view)
    assert edit_menu.capabilities(table.view)["copy"]
    edit_menu.run("copy", table.view)
    lines = QGuiApplication.clipboard().text().splitlines()
    assert len(lines) == 2 and lines[0].split("\t")[:2] == ["a", "ns"] and lines[1].split("\t")[3] == "Pending"


def test_menu_acts_on_the_last_used_widget_and_enables_by_context(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    box = QLineEdit("abc")
    qtbot.addWidget(box)
    win.edit_actions._last = box  # what focus tracking records when a menu takes focus
    box.selectAll()
    win.edit_actions.refresh()
    actions = win.edit_actions.actions
    assert actions["copy"].isEnabled() and actions["cut"].isEnabled() and not actions["redo"].isEnabled()
    actions["copy"].trigger()
    assert QGuiApplication.clipboard().text() == "abc"
    win.edit_actions._last = None
    win.edit_actions.refresh()
    assert not any(a.isEnabled() for a in actions.values())
    win.edit_actions._enable_all()  # shortcuts must keep working while the menu is closed
    assert all(a.isEnabled() for a in actions.values())


# ---- kubeconfig ---------------------------------------------------------------------------------
def test_kubeconfig_setting_persists_and_ignores_missing_files(tmp_path):
    settings = store(tmp_path)
    path = kubeconfig_file(tmp_path)
    first = KubeconfigSettings(settings)
    assert first.path is None
    first.set_path(path)
    assert KubeconfigSettings(store(tmp_path)).path == path
    with pytest.raises(ValueError):
        first.set_path(str(tmp_path / "nope"))
    assert first.path == path  # a failed change keeps the old value
    first.set_path(None)
    assert KubeconfigSettings(store(tmp_path)).path is None
    settings.setValue("kubeconfig/path", str(tmp_path / "deleted"))
    settings.sync()
    gone = KubeconfigSettings(store(tmp_path))
    assert gone.path is None and gone.missing == str(tmp_path / "deleted")


def test_service_reads_contexts_and_connects_with_the_custom_kubeconfig(monkeypatch):
    seen = {}

    def list_contexts(config_file=None):
        seen["contexts"] = config_file
        return [{"name": "a"}, {"name": "b"}], {"name": "b"}

    class Stop(Exception):
        pass

    def load(config_file=None, context=None, client_configuration=None):
        seen["load"] = (config_file, context)
        raise Stop

    monkeypatch.setattr(client_module.config, "list_kube_config_contexts", list_contexts)
    monkeypatch.setattr(client_module.config, "load_kube_config", load)
    service = KubeService("/tmp/custom")
    assert service.load_contexts() == (["a", "b"], "b") and seen["contexts"] == "/tmp/custom"
    with pytest.raises(Stop):
        service.connect("b")
    assert seen["load"] == ("/tmp/custom", "b")
    assert KubeService().kubeconfig is None


def test_kubectl_commands_use_the_custom_kubeconfig_only_when_set(qtbot):
    assert kubectl_args("shell", "c", "ns", "p")[:3] == ["kubectl", "--context", "c"]
    argv = kubectl_args("shell", "c", "ns", "p", kubeconfig="/k/cfg")
    assert argv[:5] == ["kubectl", "--kubeconfig", "/k/cfg", "--context", "c"]
    assert node_shell_args("c", "n", kubeconfig="/k/cfg")[:5] == ["kubectl", "--kubeconfig", "/k/cfg", "--context", "c"]
    assert node_shell_args("c", "n")[:3] == ["kubectl", "--context", "c"]
    dialog = DrainDialog("c", "n", kubeconfig="/k/cfg")
    qtbot.addWidget(dialog)
    assert dialog.command()[:5] == ["kubectl", "--kubeconfig", "/k/cfg", "--context", "c"]


# ---- Preferences ---------------------------------------------------------------------------------
def test_preferences_dialog_picks_default_or_a_file_and_rejects_missing(qtbot, tmp_path, monkeypatch):
    settings = KubeconfigSettings(store(tmp_path))
    dialog = PreferencesDialog(settings, tmp_path / "cfgdir")
    qtbot.addWidget(dialog)
    assert dialog.chosen_kubeconfig() is None and "Default" in dialog.path.placeholderText()
    path = kubeconfig_file(tmp_path)
    monkeypatch.setattr(preferences_module.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path, "")))
    dialog._browse()
    assert dialog.chosen_kubeconfig() == path
    dialog.path.setText(str(tmp_path / "missing"))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    dialog._save()
    assert warned and "not found" in warned[0] and dialog.result() != QDialog.Accepted
    dialog.path.clear()
    assert dialog.chosen_kubeconfig() is None


def test_open_config_folder_button_opens_the_settings_folder(qtbot, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(preferences_module.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url) or True))
    folder = tmp_path / "nested" / "config"
    dialog = PreferencesDialog(KubeconfigSettings(store(tmp_path)), folder)
    qtbot.addWidget(dialog)
    dialog.open_button.click()
    assert [Path(u.toLocalFile()) for u in opened] == [folder] and folder.is_dir()
    monkeypatch.setattr(preferences_module.QDesktopServices, "openUrl", staticmethod(lambda url: False))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))
    dialog.open_button.click()
    assert warned and "Could not open" in warned[0]


def test_changing_kubeconfig_closes_clusters_reloads_contexts_and_reaches_kubectl(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    ws = win.workspace("ctx")
    assert ws.kubeconfig is None
    path = kubeconfig_file(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.No))
    assert win.apply_kubeconfig(path) is False and win.workspaces and win.kubeconfig_settings.path is None
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.Yes))
    assert win.apply_kubeconfig(path) is True
    assert not win.workspaces and win.kubeconfig_settings.path == path
    assert "1 context" in win.statusBar().currentMessage()
    win.open_cluster("ctx")
    reopened = win.workspace("ctx")
    assert reopened.kubeconfig == path
    assert kubectl_args("shell", "ctx", "ns", "p", kubeconfig=reopened.kubeconfig)[1:3] == ["--kubeconfig", path]
    assert LabelSettings(store(tmp_path)).store.value("kubeconfig/path") == path  # saved for the next start
    win.apply_kubeconfig(None)  # back to the default; nothing open now, so no confirmation
    assert win.kubeconfig_settings.path is None


def test_preferences_action_applies_the_dialog_choice(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    path = kubeconfig_file(tmp_path)

    class FakeDialog:
        Accepted = QDialog.Accepted

        def __init__(self, kubeconfig, folder, parent=None, **pages):
            assert kubeconfig is win.kubeconfig_settings and Path(folder) == win.config_folder()

        def exec(self):
            return QDialog.Accepted

        def chosen_theme(self):
            return None

        def chosen_kubeconfig(self):
            return path

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "PreferencesDialog", FakeDialog)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.Yes))
    win.preferences_action.trigger()
    assert win.kubeconfig_settings.path == path and not win.workspaces
