"""Navigator hierarchy styling stays distinct while preserving compact, expandable browsing."""
from klotse.ui.cluster_groups import ClusterGroups
from klotse.ui.navigator import Navigator


def test_sections_categories_and_leaf_views_have_distinct_compact_hierarchy(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod/eu", "prod/us"])
    nav.set_state("prod/eu", "connected")
    nav.show_views("prod/eu")

    connected, disconnected = nav.topLevelItem(0), nav.topLevelItem(1)
    cluster = nav.cluster_item("prod/eu")
    workload = next(cluster.child(i) for i in range(cluster.childCount())
                    if cluster.child(i).text(0) == "Workloads")
    leaf = workload.child(0)

    assert connected.data(0, nav.ROLE_KIND) == "header"
    assert disconnected.data(0, nav.ROLE_KIND) == "header"
    assert workload.data(0, nav.ROLE_KIND) == nav.RESOURCE_GROUP
    assert leaf.data(0, nav.ROLE_KIND) == "view"
    assert connected.sizeHint(0).height() > workload.sizeHint(0).height() > leaf.sizeHint(0).height()
    assert workload.isExpanded() and not next(
        cluster.child(i) for i in range(cluster.childCount()) if cluster.child(i).text(0) == "Config"
    ).isExpanded()

    opened = []
    nav.view_requested.connect(lambda context, key: opened.append((context, key)))
    nav.itemClicked.emit(leaf, 0)
    assert opened == [("prod/eu", "workloads")]


def test_group_folders_keep_expansion_and_long_context_names_remain_available(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    contexts = [f"production/europe/cluster-{i:03d}-with-a-long-context-name" for i in range(36)]
    groups = ClusterGroups()
    for context in contexts:
        groups.assign(context, "production/europe")
    nav.set_contexts(contexts, groups=groups)

    folder = nav._group_items["production"]
    folder.setExpanded(False)
    nav._layout_idle()
    assert not nav._group_items["production"].isExpanded()
    assert nav._group_items["production/europe"].childCount() == len(contexts)
    assert nav.cluster_item(contexts[-1]).toolTip(0) == "Group: production/europe"

    nav.set_alias(contexts[-1], "EU production")
    assert "Context: " + contexts[-1] in nav.cluster_item(contexts[-1]).toolTip(0)
    assert nav.reveal_cluster(contexts[-1])
    assert nav._group_items["production"].isExpanded()


def test_many_connected_clusters_keep_independent_expanded_resource_trees(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    contexts = [f"cluster-{i:03d}" for i in range(24)]
    nav.set_contexts(contexts)
    for context in contexts:
        nav.set_state(context, "connected")
        nav.show_views(context)

    assert nav._connected_hdr.childCount() == len(contexts)
    assert all(nav.cluster_item(context).isExpanded() for context in contexts)
    assert all(nav.cluster_item(context).childCount() == nav.cluster_item(contexts[0]).childCount()
               for context in contexts)
