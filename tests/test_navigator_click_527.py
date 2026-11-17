"""#527: while clusters are collapsed, a double-click on a cluster row must not expand it (the click brings its overview forward).
Qt expands a row on double-click unless expandsOnDoubleClick is off, so the tree's setting carries the rule."""
from klotse.ui.navigator import Navigator


def make_nav(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.show()
    nav.set_contexts(["prod/eu", "dev"])
    nav.set_state("prod/eu", "connected")
    nav.show_views("prod/eu")
    return nav


def test_collapsed_clusters_do_not_expand_on_double_click(qtbot):
    nav = make_nav(qtbot)
    assert nav.expandsOnDoubleClick()
    nav.collapse_clusters()
    assert not nav.expandsOnDoubleClick()


def test_expanding_clusters_again_restores_double_click_expansion(qtbot):
    nav = make_nav(qtbot)
    nav.collapse_clusters()
    nav.expand_clusters()
    assert nav.expandsOnDoubleClick()
