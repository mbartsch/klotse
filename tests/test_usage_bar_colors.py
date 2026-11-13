"""CPU and Memory bars share one colour pattern; the colour changes with how full the bar is (amber above 75%, red above 90%)."""
import pytest
from PySide6.QtWidgets import QWidget

from klotse.ui import theme
from klotse.ui.overview import UsageBar, usage_color_key


@pytest.mark.parametrize("fraction, key", [(0.0, "ACCENT_HI"), (0.75, "ACCENT_HI"), (0.751, "WARN"), (0.9, "WARN"), (0.901, "BAD"), (1.0, "BAD")])
def test_the_colour_follows_how_full_the_bar_is(fraction, key):
    assert usage_color_key(fraction, "ACCENT_HI") == key


def test_memory_and_cpu_use_the_same_base_colour(qtbot):
    cpu, mem = UsageBar("CPU in use", "ACCENT_HI", thresholds=True), UsageBar("Memory in use", "ACCENT_HI", thresholds=True)
    qtbot.addWidget(cpu)
    qtbot.addWidget(mem)
    assert cpu.color_key == mem.color_key == "ACCENT_HI" and cpu.thresholds and mem.thresholds


def test_a_bar_without_thresholds_keeps_its_colour(qtbot):
    pods = UsageBar("Pods", "OK")
    qtbot.addWidget(pods)
    pods.set_usage(95, 100, "95 of 100")
    assert not pods.thresholds and pods.color_key == "OK"


def test_the_warning_and_failing_colours_exist_in_the_theme():
    assert hasattr(theme, "WARN") and hasattr(theme, "BAD") and hasattr(theme, "ACCENT_HI")
