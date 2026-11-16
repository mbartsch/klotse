"""#502: a detached cluster window is remembered at exit, disconnects cleanly, re-attaches under its alias, and closing
a cluster asks before it stops port forwards."""
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService, open_active


def window(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    return win


def test_disconnecting_a_detached_cluster_closes_its_window_and_does_not_reattach_it(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    context = ws.context
    assert win._detach_cluster(0)
    detached = win._detached[context]

    win._discard(context)

    assert context not in win._detached and context not in win.workspaces
    assert win.clusters.count() == 0
    qtbot.waitUntil(lambda: not detached.isVisible(), timeout=2000)


def test_a_reattached_cluster_tab_uses_its_alias(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    win.aliases.set(ws.context, "Production")
    assert win._detach_cluster(0)
    win._detached[ws.context].close()
    qtbot.waitUntil(lambda: win.clusters.count() == 1, timeout=2000)
    assert win.clusters.tabText(0) == "Production"


def test_exit_asks_about_a_cluster_that_is_only_in_a_detached_window(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    win.show()
    open_active(win)
    ws = win.clusters.currentWidget()
    assert win._detach_cluster(0)
    asked = []

    def answer(*args):
        asked.append(args[2])
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "question", answer)
    assert win.confirm_exit() is False
    assert ws.context in asked[0]


def test_closing_a_cluster_with_a_running_forward_asks_first(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    forward = SimpleNamespace(namespace="shop", name="web-1", remote=8080)
    monkeypatch.setattr(ws.port_forwards, "active", lambda: [forward])
    answers = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.append(a[2]) or QMessageBox.No)

    win._close_cluster_tab(win.clusters.indexOf(ws))
    assert win.workspaces.get(ws.context) is ws and "shop/web-1 port 8080" in answers[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    win._close_cluster_tab(win.clusters.indexOf(ws))
    assert ws.context not in win.workspaces


def test_closing_a_cluster_without_forwards_does_not_ask(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    ws = win.clusters.currentWidget()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: (_ for _ in ()).throw(AssertionError("asked")))
    win._close_cluster_tab(win.clusters.indexOf(ws))
    assert ws.context not in win.workspaces


def test_a_size_filter_without_its_dialog_ignores_events_instead_of_raising():
    # #502 CI: a stale size filter raised AttributeError inside Qt's event loop, which failed whatever test ran next
    from PySide6.QtCore import QEvent, QObject
    from klotse.ui.dialog_sizing import _SizeMemory
    filter_ = _SizeMemory.__new__(_SizeMemory)
    QObject.__init__(filter_)
    assert filter_.eventFilter(QObject(), QEvent(QEvent.Hide)) is False
