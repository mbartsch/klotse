"""#501: a navigator row is never shorter than its text, so a larger system font does not clip it."""
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from klotse.ui import navigator
from klotse.ui.navigator import Navigator


def test_a_row_is_at_least_as_tall_as_the_text_with_a_large_font(qtbot, monkeypatch):
    big = QFont(QApplication.font())
    big.setPointSizeF(24)
    monkeypatch.setattr(QApplication, "font", lambda *a: big)
    assert navigator.row_height(24) >= QFontMetrics(big).height() + 8
    assert navigator.row_height(24) > 24
