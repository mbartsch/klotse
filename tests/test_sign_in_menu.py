"""Sign in with the helper: offered for a cluster that is not connected; after a Yes it runs the helper's command in a terminal, and
nothing runs without the Yes."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.connect_help import auth_info
from klotse.ui import main_window as mw
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator
from test_app import FakeService

KUBE = ("contexts:\n- name: eks/eu\n  context: {cluster: eks-eu, user: eks-user}\n"
        "clusters:\n- name: eks-eu\n  cluster: {server: 'https://eu.example.test'}\n"
        "users:\n- name: eks-user\n  user:\n    exec: {command: aws, args: [eks, get-token, --cluster-name, eu, --profile, work]}\n")


def test_the_cluster_menu_offers_sign_in_only_when_not_connected(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["eks/eu"])
    texts = lambda: [a.text() for a in nav.menu_for(nav.cluster_item("eks/eu")).actions()]
    assert "Sign in with the helper…" in texts()
    nav.set_state("eks/eu", "connected")
    assert "Sign in with the helper…" not in texts()


def window(qtbot, tmp_path, monkeypatch, answer):
    kube = tmp_path / "config"
    kube.write_text(KUBE, encoding="utf-8")
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    win.kubeconfig_settings.path = str(kube)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: answer)
    return win


def test_yes_runs_the_helper_sign_in_in_a_terminal_with_the_profile(qtbot, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(mw, "open_terminal", lambda argv, mac="Terminal": opened.append((argv, mac)))
    win = window(qtbot, tmp_path, monkeypatch, QMessageBox.Yes)
    win.sign_in("eks/eu")
    qtbot.waitUntil(lambda: bool(opened))
    assert opened == [(["aws", "sso", "login", "--profile", "work"], win.terminal_settings.mac_terminal)]
    assert "eks/eu" not in win.workspaces  # signing in does not connect


def test_no_runs_nothing(qtbot, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(mw, "open_terminal", lambda argv, mac="Terminal": opened.append(argv))
    win = window(qtbot, tmp_path, monkeypatch, QMessageBox.No)
    win.sign_in("eks/eu")
    assert opened == []


def test_a_cluster_without_a_helper_says_so(qtbot, tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: told.append(args[2]))
    win = window(qtbot, tmp_path, monkeypatch, QMessageBox.Yes)
    win.kubeconfig_settings.path = str(tmp_path / "config")  # the same file; the context has a helper, so write a plain one
    (tmp_path / "config").write_text("contexts:\n- name: plain\n  context: {cluster: c, user: u}\nclusters:\n- name: c\n"
                                     "  cluster: {server: 'https://a.test'}\nusers:\n- name: u\n  user: {token: x}\n", encoding="utf-8")
    assert auth_info(win.kubeconfig_settings.path, "plain")["exec"] == ""
    win.sign_in("plain")
    assert told and "names no sign-in helper" in told[0]
