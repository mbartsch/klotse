import pytest
from PySide6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def no_blocking_exit_question(monkeypatch):
    """Closing a shown MainWindow with clusters open asks about remembering them; a modal question would hang a test
    that never expects it. Tests that care patch QMessageBox.question themselves, which replaces this."""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
