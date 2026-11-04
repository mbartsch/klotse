from klotse.ui.overview import CLUSTER_CARDS, OverviewView
from klotse.ui.workspace import ClusterWorkspace

DATA = {"counts": {"nodes": 3}, "events": [], "lists": {}}


def shown(widget):
    return not widget.isHidden()


def on_loading_page(view):
    return view.stack.currentWidget() is view.loading_page


def test_first_load_shows_a_busy_indicator_until_data_arrives(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    assert not on_loading_page(view)
    view.set_loading()
    assert on_loading_page(view) and shown(view.loading_bar)
    assert view.loading_bar.minimum() == view.loading_bar.maximum() == 0  # indeterminate
    assert view.loading_label.text().startswith("Loading cluster data")
    view._loading_tick()
    assert view.loading_label.text().endswith("0s")
    view.set_data(DATA)
    assert not on_loading_page(view) and not view._load_timer.isActive()


def test_routine_refreshes_stay_quiet(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.set_data(DATA)
    view.set_loading()
    assert not on_loading_page(view) and not view._load_timer.isActive()


def test_a_failed_first_load_says_so_and_a_retry_shows_the_indicator_again(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.set_loading()
    view.set_error("ReadTimeoutError")
    assert view.loading_label.text() == "Load failed: ReadTimeoutError"
    assert on_loading_page(view) and not shown(view.loading_bar) and not view._load_timer.isActive()
    view.set_loading()
    assert shown(view.loading_bar) and view.loading_label.text().startswith("Loading cluster data")


def test_an_error_while_data_is_shown_leaves_the_page_alone(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.set_data(DATA)
    view.set_error("boom")
    assert not on_loading_page(view)


def test_workspace_load_drives_the_overview_indicator(qtbot):
    ws = ClusterWorkspace(None, "test")
    qtbot.addWidget(ws)
    pending = []
    ws._run = lambda fn, ok, *args, on_error=None: pending.append((ok, on_error))
    ws.loaders["overview"] = (lambda ns: DATA, False, ws.overview.set_data)
    ws.load_view("overview")
    assert on_loading_page(ws.overview)
    pending.pop()[1]("ReadTimeoutError")
    assert ws.overview.loading_label.text() == "Load failed: ReadTimeoutError"
    ws.load_view("overview")
    pending.pop()[0](DATA)
    assert not on_loading_page(ws.overview)
