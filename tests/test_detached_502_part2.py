"""#502 part 2: the Port forwards tab, log streams and tool windows are asked about before a cluster closes; one notice for
every cluster that fails to connect; Preferences checks every page before it saves one, and asks about the kubeconfig
before it saves anything; a detached window has its own status bar and the main menus' shortcuts."""
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.proxy_settings import ProxySettings
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.preferences import KubeconfigSettings, PreferencesDialog
from test_app import FakeService, open_active


def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def qsettings(tmp_path):
    return QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)


def test_running_work_counts_forwards_streams_and_open_windows(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    forward = SimpleNamespace(namespace="shop", name="web-1", remote=8080)
    monkeypatch.setattr(ws.port_forwards, "active", lambda: [forward])
    forwards, streams, windows = ws.running_work()
    assert forwards == [forward] and streams == 0 and windows == []


def test_confirm_stop_work_asks_when_something_runs_and_stays_quiet_otherwise(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[2]) or QMessageBox.No)
    monkeypatch.setattr(ws, "running_work", lambda: ([], 2, []))
    assert win._confirm_stop_work(ws, "reconnect") is False
    assert "2 log stream(s)" in asked[0] and "Reconnect stops them" in asked[0]
    monkeypatch.setattr(ws, "running_work", lambda: ([], 0, []))
    assert win._confirm_stop_work(ws, "close") is True and len(asked) == 1


def test_closing_the_port_forwards_tab_with_running_forwards_asks_first(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    ws.show_port_forwards()
    index = next(i for i in range(ws.log_tabs.count()) if ws.log_tabs.tabText(i) == "Port forwards")
    monkeypatch.setattr(ws.port_forwards, "active", lambda: [SimpleNamespace(namespace="shop", name="web-1", remote=8080)])
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    ws._close_log_tab(index)
    assert ws.log_tabs.tabText(index) == "Port forwards"


def test_failed_connections_share_one_notice(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "show", lambda self: shown.append(self.text()))
    win._on_failed("eu", "VPN is down")
    win._on_failed("us", "VPN is down")
    win._show_failures()
    assert win._connection_prompts["eu"] is win._connection_prompts["us"]
    assert len(shown) == 1 and "eu: VPN is down" in shown[0] and "us: VPN is down" in shown[0]
    win._connection_prompts["eu"].close()


def test_one_failed_connection_keeps_the_single_cluster_notice(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "show", lambda self: shown.append((self.windowTitle(), self.text())))
    win._on_failed("eu", "refused")
    win._show_failures()
    assert shown == [("Cannot connect", "eu\n\nrefused")]
    win._connection_prompts["eu"].close()


def test_a_bad_proxy_stops_preferences_before_any_page_is_written(qtbot, tmp_path, monkeypatch):
    store = qsettings(tmp_path)
    proxy = ProxySettings(store)
    dialog = PreferencesDialog(KubeconfigSettings(store), tmp_path / "cfg", proxy=proxy)
    qtbot.addWidget(dialog)
    dialog.proxy_url.setText("ftp://proxy.corp:21")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    dialog._save()
    assert warnings and warnings[0].startswith("Proxy:") and "Nothing was saved." in warnings[0]
    assert not store.value("network/proxy")


def test_a_changed_kubeconfig_is_asked_before_any_page_is_saved(qtbot, tmp_path):
    store = qsettings(tmp_path)
    proxy = ProxySettings(store)
    kubeconfig_file = tmp_path / "other.yaml"
    kubeconfig_file.write_text("apiVersion: v1\n", encoding="utf-8")
    asked = []

    def decline():
        asked.append(True)
        return False

    dialog = PreferencesDialog(KubeconfigSettings(store), tmp_path / "cfg", proxy=proxy, confirm_kubeconfig=decline)
    qtbot.addWidget(dialog)
    dialog.path.setText(str(kubeconfig_file))
    dialog.proxy_url.setText("http://proxy.corp:3128")
    dialog._save()
    assert asked == [True]
    assert not store.value("network/proxy")  # declined: the proxy page was not written either


def test_a_detached_window_shows_its_messages_and_has_the_main_shortcuts(qtbot, tmp_path):
    from PySide6.QtGui import QAction, QKeySequence
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    probe = QAction("probe", win)
    probe.setShortcut(QKeySequence("Ctrl+Shift+Y"))
    win.addAction(probe)
    assert win._detach_cluster(0)
    detached = win._detached[ws.context]
    ws.message.emit("Loaded pods")
    assert detached.statusBar().currentMessage() == "Loaded pods"
    assert probe in detached.actions()
    detached.close()
    qtbot.waitUntil(lambda: win.clusters.count() == 1, timeout=2000)
