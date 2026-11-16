"""#501: column widths and dialog sizes are kept between starts; a width or size the user never set is not."""
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from PySide6.QtCore import QSettings, QSize

from klotse.k8s.models import PodRow
from klotse.ui import dialog_sizing, resource_table
from klotse.ui.context_dialog import ContextEntryDialog
from klotse.ui.resource_table import ResourceTable

COLUMNS = [("Name", "name"), ("Restarts", "restarts")]


def pods():
    return [PodRow(f"pod-{i}", "ns", "1/1", "Running", i, "", "") for i in range(3)]


def test_a_dragged_column_width_is_kept_for_the_next_table_of_that_kind(qtbot, tmp_path, monkeypatch):
    store = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    monkeypatch.setattr(resource_table, "_settings", store)
    first = ResourceTable(COLUMNS, "Pod")
    qtbot.addWidget(first)
    first.set_rows(pods())
    first.view.horizontalHeader().resizeSection(0, 333)
    second = ResourceTable(COLUMNS, "Pod")
    qtbot.addWidget(second)
    second.set_rows(pods())
    assert second.view.columnWidth(0) == 333
    other = ResourceTable(COLUMNS, "Node")
    qtbot.addWidget(other)
    other.set_rows(pods())
    assert other.view.columnWidth(0) != 333


def test_a_dialog_keeps_the_size_it_was_closed_at(qtbot, tmp_path, monkeypatch):
    store = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    monkeypatch.setattr(dialog_sizing, "_settings", store)
    first = ContextEntryDialog("prod", ["a: b"])
    qtbot.addWidget(first)
    first.show()
    first.resize(500, 400)
    first.hide()
    second = ContextEntryDialog("prod", ["a: b"])
    qtbot.addWidget(second)
    assert second.size() == QSize(500, 400)


def test_the_open_views_are_remembered_with_the_session_and_open_again(qtbot, tmp_path):
    from klotse.ui.cluster_session import ClusterSession
    from test_app import FakeService
    settings = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    session = ClusterSession(settings)
    session.remember(["ctx"], "ctx", {}, {"ctx": ["overview", "nodes"]})
    assert session.view_keys() == {"ctx": ["overview", "nodes"]}
    win = MainWindow(FakeService, label_settings=LabelSettings(settings))
    qtbot.addWidget(win)
    win.restore_clusters()
    ws = win.workspaces["ctx"]
    assert "nodes" in ws.static_view_keys()
