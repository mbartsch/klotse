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
    assert "Group: production/europe" in nav.cluster_item(contexts[-1]).toolTip(0)

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


def test_cluster_states_are_textual_aliases_keep_context_and_failure_detail(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod/eu"], aliases={"prod/eu": "EU production"})
    item = nav.cluster_item("prod/eu")

    assert "Disconnected" in item.text(0)
    assert "EU production" in item.text(0) and "[prod/eu]" in item.text(0)
    assert "Status: Disconnected" in item.toolTip(0)

    nav.set_state("prod/eu", "connecting")
    assert "Connecting" in item.text(0)
    assert item.parent() is nav._connected_hdr
    nav.set_state("prod/eu", "connected")
    assert "Connected" in item.text(0)

    nav.set_state("prod/eu", "failed", "Unauthorized: token expired")
    assert "Failed" in item.text(0)
    assert item.parent() is nav._idle_hdr
    assert "Connection error:" in item.toolTip(0)
    assert "Unauthorized: token expired" in item.toolTip(0)
    assert "Alias: EU production" in item.toolTip(0)
    assert "Context: prod/eu" in item.toolTip(0)

    nav.set_state("prod/eu", "idle")
    assert "Disconnected" in item.text(0)
    assert "Unauthorized" not in item.toolTip(0)
