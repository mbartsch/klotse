from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from klotse.ui.cluster_overview import ClusterOverview


class FakeWorkspace(QObject):
    overview_updated = Signal(object)
    state = Signal(str)

    def __init__(self):
        super().__init__()
        self.overview = SimpleNamespace(_data=None)


def overview(nodes=3, ready=2, running=8, pending=1, failed=0, warnings=0):
    return {"counts": {"nodes": nodes, "nodes_ready": ready, "running": running, "pending": pending,
                        "failed": failed, "warnings": warnings}}


def test_all_clusters_shows_paired_metrics_and_attention(qtbot):
    page = ClusterOverview()
    qtbot.addWidget(page)
    workspace = FakeWorkspace()
    page.set_workspaces({"prod": workspace}, {"prod": "Production"})

    workspace.overview_updated.emit(overview())

    assert page.table.item(0, 0).text() == "Production"
    assert page.table.item(0, 1).text() == "Needs attention"
    assert page.table.item(0, 2).text() == "2 / 3"
    assert page.table.item(0, 3).text() == "8 / 1"
    assert "1 needs attention" in page.summary.text()
    page.fit_button.click()


def test_all_clusters_filter_and_actions_use_context(qtbot):
    page = ClusterOverview()
    qtbot.addWidget(page)
    first, second = FakeWorkspace(), FakeWorkspace()
    page.set_workspaces({"prod": first, "staging": second}, {"prod": "Production", "staging": "Staging"})
    first.overview_updated.emit(overview(ready=3, nodes=3))
    second.overview_updated.emit(overview(ready=2, nodes=2))

    opened, refreshed = [], []
    page.open_requested.connect(opened.append)
    page.refresh_requested.connect(refreshed.append)
    page.search.setText("stag")
    assert page.table.isRowHidden(0)
    assert not page.table.isRowHidden(1)
    page.table.selectRow(1)
    page.open_button.click()
    page.refresh_cluster_button.click()

    assert opened == ["staging"]
    assert refreshed == ["staging"]


def test_all_clusters_can_detach_and_reattach(qtbot):
    from klotse.ui.main_window import MainWindow
    from test_app import FakeService

    window = MainWindow(FakeService)
    qtbot.addWidget(window)
    window.show_all_clusters()
    assert window._detach_all_clusters()
    assert window.clusters.indexOf(window.cluster_overview) < 0
    detached = window._all_clusters_detached
    detached.close()
    qtbot.waitUntil(lambda: window.clusters.indexOf(window.cluster_overview) >= 0)
