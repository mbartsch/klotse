"""The cluster overview's Dashboard / Workloads switch: both buttons look like buttons, and the view in front is the filled one."""
from klotse.ui import theme
from klotse.ui.overview import CLUSTER_CARDS, OverviewView, switch_button_style


def test_the_switch_style_frames_both_buttons_and_fills_the_checked_one():
    style = switch_button_style()
    assert "border: 1px solid" in style and "border-radius" in style
    assert f"QPushButton:checked {{ background: {theme.ACCENT}" in style


def test_both_switch_buttons_have_the_frame_and_dashboard_is_the_one_in_front(qtbot):
    view = OverviewView(CLUSTER_CARDS)
    qtbot.addWidget(view)
    view.show()
    assert view.dashboard_button.isChecked() and not view.workloads_button.isChecked()
    for button in (view.dashboard_button, view.workloads_button):
        assert "border: 1px solid" in button.styleSheet()
    view.refresh_theme()
    assert "border: 1px solid" in view.workloads_button.styleSheet()
