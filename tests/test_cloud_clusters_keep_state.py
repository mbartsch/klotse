"""#485: adding clusters from the cloud must not move connected clusters under Disconnected."""
from klotse.ui.navigator import Navigator


def section_of(nav, name):
    stack = [nav.topLevelItem(i) for i in range(nav.topLevelItemCount())]
    while stack:
        node = stack.pop()
        if node.data(0, nav.ROLE_KIND) == "cluster" and node.data(0, nav.ROLE_KEY) == name:
            return node.parent().text(0).split(" (")[0].title()  # headers carry a count
        stack.extend(node.child(i) for i in range(node.childCount()))
    raise AssertionError(name)


def test_connected_and_failed_clusters_stay_under_their_section_after_a_reload(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod", "dev", "qa"])
    nav.set_state("prod", "connected")
    nav.set_state("qa", "failed", "boom")
    snapshot = nav.state_snapshot()
    nav.set_contexts(["prod", "dev", "qa", "new-cloud"])  # what adding clusters does
    nav.restore_states(snapshot)
    assert section_of(nav, "prod") == "Connected"
    assert section_of(nav, "qa") != "Connected"
    assert section_of(nav, "dev") != "Connected"
    assert section_of(nav, "new-cloud") != "Connected"  # new entries are listed, not connected
    assert nav._states["qa"] == "failed" and nav._failures["qa"] == "boom"
