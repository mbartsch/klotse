"""#501: chart series colours read on light and dark backgrounds; font sizes scale with the system font."""
from PySide6.QtGui import QColor

from klotse.ui import line_chart, theme
from klotse.ui.theme import font_pt


def test_the_series_colours_are_the_light_set_on_a_light_theme(qapp, monkeypatch):
    monkeypatch.setattr(theme, "BG", "#ffffff")
    assert line_chart.LineChart.colour(0) == QColor(line_chart.PALETTE_LIGHT[0])
    monkeypatch.setattr(theme, "BG", "#16232b")
    assert line_chart.LineChart.colour(0) == QColor(line_chart.PALETTE[0])


def test_font_sizes_are_points_scaled_from_the_system_font(qapp, monkeypatch):
    from PySide6.QtGui import QFont
    monkeypatch.setattr(qapp, "font", lambda: QFont("Segoe UI", 9))
    assert font_pt(12) == "9.00pt"
    monkeypatch.setattr(qapp, "font", lambda: QFont("Segoe UI", 18))
    assert font_pt(12) == "18.00pt"
