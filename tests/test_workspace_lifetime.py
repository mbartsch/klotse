import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent

from klotse.ui.overview import CLUSTER_CARDS, WORKLOAD_CARDS, OverviewView
from klotse.ui.workspace import ClusterWorkspace


def test_deleting_workspace_destroys_opened_and_unopened_views(qapp):
    workspace = ClusterWorkspace(None, "lifetime-test")
    opened = workspace.pod_table
    workspace._show_view("pods")
    custom_key = workspace._custom_view("example.org", "v1", "widgets", "Widget", True)
    views = [view for view, _ in workspace.views.values()]
    assert workspace.tabs.indexOf(opened) >= 0
    assert workspace.tabs.indexOf(workspace.views[custom_key][0]) == -1

    workspace.shutdown()
    workspace.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    assert not shiboken6.isValid(workspace)
    assert all(not shiboken6.isValid(view) for view in views)


@pytest.mark.parametrize("cards", [CLUSTER_CARDS, WORKLOAD_CARDS])
def test_deleting_overview_destroys_hidden_cards_and_heading(qapp, cards):
    overview = OverviewView(cards)
    owned = [*overview.cards.values(), overview.title]
    overview.show()
    if overview.is_cluster_overview:
        assert overview.cards["nodes"].isVisible()
        assert overview.cards["failed"].isHidden()
    else:
        assert overview.cards["failed"].isVisible()
    overview.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert all(not shiboken6.isValid(widget) for widget in owned)
