"""The CONNECTED section's right-click menu turns auto-refresh off, on, or back to the saved settings for every open cluster."""
from test_auto_refresh import add_workspace, make_window, make_ws


def connected_menu(window):
    return window.nav.menu_for(window.nav._connected_hdr)


def test_the_connected_header_offers_pause_enable_and_restore(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    labels = [action.text() for action in connected_menu(window).actions()]
    assert labels == ["Turn auto-refresh off on all connected clusters", "Turn auto-refresh on on all connected clusters",
                      "Restore each cluster's previous auto-refresh"]


def test_pause_then_restore_from_the_connected_header(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    ws, _loads = make_ws(qtbot)
    ws.auto_action.setChecked(True)
    add_workspace(window, ws, "alpha")
    menu = connected_menu(window)
    menu.actions()[0].trigger()  # pause
    assert window._global_auto_paused and not ws.auto_action.isChecked()
    menu.actions()[2].trigger()  # restore
    assert not window._global_auto_paused and ws.auto_action.isChecked()


def test_enable_from_the_connected_header_turns_refresh_on_everywhere(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    ws, _loads = make_ws(qtbot)
    ws.auto_action.setChecked(False)
    add_workspace(window, ws, "alpha")
    connected_menu(window).actions()[1].trigger()  # enable
    assert not window._global_auto_paused and ws.auto_action.isChecked()


def test_a_connected_cluster_menu_enables_or_disables_its_auto_refresh(qtbot):
    from klotse.ui.navigator import Navigator
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a", "b"])
    nav.set_state("a", "connected")
    sent = []
    nav.auto_refresh_clusters_requested.connect(lambda names, on: sent.append((list(names), on)))
    menu = nav.menu_for(nav.cluster_item("a"))
    next(x for x in menu.actions() if x.text() == "Disable auto-refresh").trigger()
    next(x for x in menu.actions() if x.text() == "Enable auto-refresh").trigger()
    assert sent == [(["a"], False), (["a"], True)]


def test_with_several_connected_clusters_selected_the_entries_act_on_the_selection(qtbot):
    from klotse.ui.navigator import Navigator
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a", "b", "c"])
    nav.set_state("a", "connected")
    nav.set_state("b", "connected")
    nav.cluster_item("a").setSelected(True)
    nav.cluster_item("b").setSelected(True)
    nav.cluster_item("c").setSelected(True)  # disconnected: not part of the selection
    menu = nav.menu_for(nav.cluster_item("a"))
    texts = [x.text() for x in menu.actions()]
    assert "Disable auto-refresh (2)" in texts and "Enable auto-refresh (2)" in texts
    sent = []
    nav.auto_refresh_clusters_requested.connect(lambda names, on: sent.append((sorted(names), on)))
    next(x for x in menu.actions() if x.text() == "Disable auto-refresh (2)").trigger()
    assert sent == [(["a", "b"], False)]


def test_the_window_switches_only_the_given_clusters_and_a_global_pause_still_wins(qtbot, tmp_path):
    window = make_window(qtbot, tmp_path)
    first, _ = make_ws(qtbot)
    second, _ = make_ws(qtbot)
    first.auto_action.setChecked(True)
    second.auto_action.setChecked(True)
    add_workspace(window, first, "first")
    add_workspace(window, second, "second")
    window._set_clusters_auto_refresh(["first"], False)
    assert not first.auto_action.isChecked() and second.auto_action.isChecked()
    window.pause_auto_refresh_all_action.trigger()
    window._set_clusters_auto_refresh(["second"], False)
    window._set_clusters_auto_refresh(["first"], True)
    assert not first.auto_action.isChecked()  # the global pause still wins
