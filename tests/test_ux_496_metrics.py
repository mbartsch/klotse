"""#496: the metrics source dialog checks what was typed while it is still open, and clearing a source asks first."""
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui.metrics_preferences import AddSourceDialog


def test_a_bad_address_keeps_the_dialog_open_with_the_text(qtbot, monkeypatch):
    dialog = AddSourceDialog(["prod"], cluster="prod", fixed_cluster=True)
    qtbot.addWidget(dialog)
    dialog.service.setEditText("not a valid address !!")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.accept()
    assert warned and not closed  # the dialog did not close, so nothing typed was lost
    assert dialog.service.currentText() == "not a valid address !!"


def test_clearing_a_source_asks_and_no_keeps_it(qtbot, monkeypatch):
    dialog = AddSourceDialog(["prod"], cluster="prod", service="monitoring/prometheus-server:80", fixed_cluster=True)
    qtbot.addWidget(dialog)
    dialog.service.setEditText("")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.No)
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.accept()
    assert asked and not closed


def test_clearing_a_source_with_yes_goes_back_to_the_default(qtbot, monkeypatch):
    dialog = AddSourceDialog(["prod"], cluster="prod", service="monitoring/prometheus-server:80", fixed_cluster=True)
    qtbot.addWidget(dialog)
    dialog.service.setEditText("")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    closed = []
    dialog.accepted.connect(lambda: closed.append(True))
    dialog.accept()
    assert closed == [True]


def test_a_new_source_with_no_cluster_says_so_before_closing(qtbot, monkeypatch):
    dialog = AddSourceDialog(["prod"], cluster="", fixed_cluster=False)
    qtbot.addWidget(dialog)
    dialog.cluster.setEditText("")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dialog.accept()
    assert warned and "Choose or type the cluster" in warned[0]
