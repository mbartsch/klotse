from PySide6.QtCore import Qt

from klotse.ui import theme
from klotse.ui.line_chart import LineChart
from klotse.ui.overview import Card


def contrast(a, b):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(channels, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + 0.05) / (low + 0.05)


def test_harbour_text_and_control_contrast():
    t = theme.HARBOUR
    for fg, bg in [(t.text, t.base), (t.muted, t.base), (t.muted, t.bg),
                   (t.text, t.selected_row), (t.ok, t.bg), (t.bad, t.bg),
                   (t.bad, t.base), (t.warn, t.bg), ("#ffffff", t.accent)]:
        assert contrast(fg, bg) >= 4.5, (fg, bg)
    for border, bg in [(t.input_border, t.base), (t.accent_hi, t.base)]:
        assert contrast(border, bg) >= 3, (border, bg)


def test_overview_card_keyboard_activation(qtbot):
    card = Card("failed", "Failed pods", "bad")
    qtbot.addWidget(card)
    card.set_clickable(True)
    card.show()
    card.setFocus()
    assert card.focusPolicy() == Qt.StrongFocus and "Card:focus" in card.styleSheet()
    with qtbot.waitSignal(card.clicked, timeout=1000) as signal:
        qtbot.keyClick(card, Qt.Key_Space)
    assert signal.args == ["failed"]


def test_chart_keyboard_series_selection_and_activation(qtbot):
    chart = LineChart("CPU")
    qtbot.addWidget(chart)
    chart.set_data({"pod-a": [(1, 1.0)], "pod-b": [(1, 2.0)]})
    chart.show()
    chart.setFocus()
    assert chart.focusPolicy() == Qt.StrongFocus
    qtbot.keyClick(chart, Qt.Key_Right)
    assert chart.selected_line == "pod-b" and "pod-b" in chart.accessibleDescription()
    with qtbot.waitSignal(chart.line_activated, timeout=1000) as signal:
        qtbot.keyClick(chart, Qt.Key_Return)
    assert signal.args == ["pod-b"]
