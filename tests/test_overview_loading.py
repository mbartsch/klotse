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
    assert on_loading_page(view) and shown(view.loading_clock)
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
    assert on_loading_page(view) and not shown(view.loading_clock) and not view._load_timer.isActive()
    view.set_loading()
    assert shown(view.loading_clock) and view.loading_label.text().startswith("Loading cluster data")


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


def make_table(kind="Event", spec="events"):
    from klotse.k8s.resources import SPEC_BY_KEY
    from klotse.ui.resource_table import ResourceTable
    return ResourceTable(SPEC_BY_KEY[spec].columns, kind, editable=False, customizable=False)


def test_a_list_shows_a_large_message_until_its_first_rows_arrive(qtbot):
    table = make_table()
    qtbot.addWidget(table)
    assert table.stack.currentWidget() is table.view
    table.set_loading()
    assert table.stack.currentWidget() is table.loading_page and table.loading_page.label.text() == "Loading…"
    table._loading_tick()
    assert table.loading_page.label.text() == "Loading… 0s" and table.status.text() == "Loading… 0s"
    table.set_rows([])
    assert table.stack.currentWidget() is table.view and table.status.text() == "No resources found"


def test_a_list_that_fails_to_load_says_so_in_the_big_message(qtbot):
    table = make_table()
    qtbot.addWidget(table)
    table.set_loading()
    table.set_error("ReadTimeoutError")
    assert table.stack.currentWidget() is table.loading_page
    assert table.loading_page.label.text() == "Load failed: ReadTimeoutError" and table.loading_page.clock.isHidden()
    table.set_loading()  # retry
    assert not table.loading_page.clock.isHidden() and table.loading_page.label.text() == "Loading…"


def test_a_reload_with_rows_on_screen_keeps_the_table_visible(qtbot):
    from klotse.k8s.models import NamespaceRow
    ws = ClusterWorkspace(None, "test")
    qtbot.addWidget(ws)
    table = ws.ns_table
    table.set_rows([NamespaceRow("default", "Active", "1d", "", {})])
    table.set_loading()  # the routine 10 s refresh
    assert table.stack.currentWidget() is table.view and table.status.text() == "Loading…"
    table.set_error("boom")
    assert table.stack.currentWidget() is table.view  # the rows stay; the status line carries the error


def test_the_clock_hands_turn_at_fixed_speeds():
    from klotse.ui.loading_pane import MINUTE_HAND_TURN_MS, hand_angles
    assert hand_angles(0) == (0.0, 0.0)
    hour, minute = hand_angles(MINUTE_HAND_TURN_MS // 4)
    assert minute == 90.0 and hour == 7.5  # the hour hand moves a twelfth as fast
    assert hand_angles(MINUTE_HAND_TURN_MS)[1] == 0.0  # one full turn, back at 12
    assert hand_angles(MINUTE_HAND_TURN_MS * 12)[0] == 0.0  # the hour hand needs twelve of them


def test_the_clock_only_ticks_while_it_is_on_screen_and_draws(qtbot):
    from klotse.ui.loading_pane import LoadingPane
    pane = LoadingPane()
    qtbot.addWidget(pane)
    assert not pane.clock._timer.isActive()
    pane.show()
    assert pane.clock._timer.isActive()
    assert not pane.clock.grab().isNull()  # painting does not raise
    pane.hide()
    assert not pane.clock._timer.isActive()


def test_the_hands_keep_their_place_when_the_clock_is_hidden_shown_or_the_text_changes(qtbot, monkeypatch):
    from klotse.ui import loading_pane
    now = [1000.0]
    monkeypatch.setattr(loading_pane, "monotonic", lambda: now[0])
    pane = loading_pane.LoadingPane()
    qtbot.addWidget(pane)
    pane.show_busy("Loading… 0s")
    pane.show()
    now[0] += 0.5
    before = pane.clock.angles()
    pane.hide()  # a tab switch, a redraw, a relayout...
    pane.show()
    pane.show_busy("Loading… 1s")  # the once-a-second tick
    assert pane.clock.angles() == before  # same moment, same hands: nothing restarted
    now[0] += 0.5
    assert pane.clock.angles() != before  # and time still moves them on
