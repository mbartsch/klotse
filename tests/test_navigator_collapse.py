"""Collapse clusters folds every cluster's children so only the cluster list shows; expand puts each cluster back as it was."""
from klotse.ui.navigator import Navigator


def menu_texts(nav, header):
    return [action.text() for action in nav.menu_for(header).actions() if action.text()]


def test_collapse_leaves_only_the_cluster_list_and_expand_restores_each_cluster(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.show()
    nav.set_contexts(["prod/eu", "prod/us", "dev"])
    nav.set_state("prod/eu", "connected")
    nav.show_views("prod/eu")
    nav.show_views("prod/us")
    nav.set_state("prod/us", "connected")
    nav.cluster_item("prod/eu").setExpanded(True)
    nav.cluster_item("prod/us").setExpanded(False)  # a cluster the user left closed

    nav.collapse_clusters()
    assert not any(nav.cluster_item(n).isExpanded() for n in ("prod/eu", "prod/us", "dev"))
    assert nav.cluster_item("prod/eu").childCount() > 0  # children still exist, only folded
    assert nav.topLevelItem(0).isExpanded() and nav.topLevelItem(1).isExpanded()  # the section headers stay open

    nav.collapse_clusters()  # a second collapse keeps the first snapshot
    nav.expand_clusters()
    assert nav.cluster_item("prod/eu").isExpanded()
    assert not nav.cluster_item("prod/us").isExpanded()
    assert not nav.cluster_item("dev").isExpanded()  # collapsed before, so it stays collapsed


def test_both_section_headers_offer_collapse_and_expand(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a"])
    assert "Collapse clusters (show only the cluster list)" in menu_texts(nav, nav.topLevelItem(0))
    assert "Expand clusters" in menu_texts(nav, nav.topLevelItem(1))
