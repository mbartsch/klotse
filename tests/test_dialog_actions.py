from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton

from klotse.ui.dialog_actions import action_box


def test_action_box_keeps_explicit_roles_and_button_callbacks(qtbot):
    parent = QDialog()
    qtbot.addWidget(parent)
    refresh = QPushButton("Refresh")
    close = QPushButton("Close")
    calls = []
    refresh.clicked.connect(lambda: calls.append("refresh"))
    close.clicked.connect(lambda: calls.append("close"))

    box = action_box(parent, (refresh, QDialogButtonBox.ActionRole),
                     (close, QDialogButtonBox.RejectRole))

    assert box.buttonRole(refresh) == QDialogButtonBox.ActionRole
    assert box.buttonRole(close) == QDialogButtonBox.RejectRole
    refresh.click()
    close.click()
    assert calls == ["refresh", "close"]
