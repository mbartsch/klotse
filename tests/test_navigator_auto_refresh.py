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
