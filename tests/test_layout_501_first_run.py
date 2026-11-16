"""#501: with no cluster open, the centre of the main window explains how to start (a new user sees it first)."""
from PySide6.QtCore import QSettings
from klotse.ui.customizations import LabelSettings
from klotse.ui.first_run import FirstRunPage
from klotse.ui.main_window import MainWindow
from test_app import FakeService, open_active


def window(tmp_path, qtbot):
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    return win


def test_with_no_cluster_open_the_centre_shows_the_first_run_page(qtbot, tmp_path):
    win = window(tmp_path, qtbot)
    assert win.clusters.count() == 0
    assert win.centre.currentWidget() is win.first_run


def test_opening_a_cluster_shows_the_tabs_and_closing_them_brings_the_page_back(qtbot, tmp_path):
    win = window(tmp_path, qtbot)
    open_active(win)
    assert win.centre.currentWidget() is win.clusters
    win.clusters.tabCloseRequested.emit(0)
    assert win.centre.currentWidget() is win.first_run


def test_the_wording_depends_on_whether_clusters_are_listed(qtbot):
    page = FirstRunPage()
    qtbot.addWidget(page)
    page.set_has_contexts(False)
    assert page.title.text() == "No clusters yet"
    assert page.choose_button.isVisibleTo(page) and page.cloud_button.isVisibleTo(page)
    page.set_has_contexts(True)
    assert page.title.text() == "No cluster is open"
    assert "navigator" in page.text.text()
    assert not page.choose_button.isVisibleTo(page) and not page.cloud_button.isVisibleTo(page)


def test_the_buttons_open_the_kubeconfig_preferences_and_the_cloud_dialog(qtbot, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(MainWindow, "open_preferences", lambda self, *a, **k: calls.append("preferences"))
    monkeypatch.setattr(MainWindow, "add_clusters_from_cloud", lambda self, *a, **k: calls.append("cloud"))
    win = window(tmp_path, qtbot)
    win.first_run.choose_kubeconfig.emit()
    win.first_run.add_from_cloud.emit()
    assert calls == ["preferences", "cloud"]
