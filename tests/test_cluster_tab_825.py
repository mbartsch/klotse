"""#825: choosing the tab of a cluster collapsed in the cluster list selects its row without expanding it and brings its
Overview to the front; expanding it later highlights that view. An expanded cluster keeps highlighting its current view."""
from test_app import FakeService

from klotse.ui.main_window import MainWindow
from klotse.ui.navigator import Navigator


class Service(FakeService):
    @staticmethod
    def load_contexts():
        return ["shop-prod", "dev-lab"], "shop-prod"


def window(qtbot):
    win = MainWindow(Service)
    qtbot.addWidget(win)
    win.resize(1200, 700)
    win.show()
    win.open_cluster("shop-prod")
    win.open_cluster("dev-lab")
    qtbot.waitUntil(lambda: all(win.nav._clusters[n].childCount() > 0 for n in ("shop-prod", "dev-lab")), timeout=5000)
    shop = win.workspace("shop-prod")
    win.clusters.setCurrentWidget(shop)
    shop.open_view("pods")
    qtbot.waitUntil(lambda: shop.tabs.currentWidget() is shop.pod_table, timeout=5000)
    return win, shop


def current_key(win):
    return win.nav.currentItem().data(0, Navigator.ROLE_KEY)


def back_to(win, shop):
    win.clusters.setCurrentWidget(win.workspace("dev-lab"))
    win.clusters.setCurrentWidget(shop)  # what clicking the cluster's tab does


def test_a_collapsed_cluster_stays_collapsed_and_shows_its_overview(qtbot):
    win, shop = window(qtbot)
    item = win.nav._clusters["shop-prod"]
    item.setExpanded(False)
    back_to(win, shop)
    assert not item.isExpanded()
    assert win.nav.currentItem() is item
    assert shop.tabs.currentWidget() is shop.overview


def test_expanding_it_afterwards_highlights_the_view_shown(qtbot):
    win, shop = window(qtbot)
    item = win.nav._clusters["shop-prod"]
    item.setExpanded(False)
    back_to(win, shop)
    item.setExpanded(True)
    assert current_key(win) == "overview"


def test_an_expanded_cluster_keeps_its_current_view(qtbot):
    win, shop = window(qtbot)
    back_to(win, shop)
    assert win.nav._clusters["shop-prod"].isExpanded()
    assert shop.tabs.currentWidget() is shop.pod_table
    assert current_key(win) == "pods"
