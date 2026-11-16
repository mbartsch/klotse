"""#496: Enter in an add field adds the item; it does not also save the dialog."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from klotse.ui.data_editor import DataEditDialog

CM = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "web", "namespace": "shop"}, "data": {"mode": "fast"}}


def test_enter_in_the_new_key_box_adds_the_key_and_keeps_the_dialog_open(qtbot):
    dlg = DataEditDialog("ConfigMap", dict(CM))
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.data.new_key.setText("token")
    QTest.keyClick(dlg.data.new_key, Qt.Key_Return)
    assert "token" in dlg.data.keys_in_use()
    assert dlg.result() != 1  # not accepted: Enter did not press Save
    assert dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).isEnabled()  # Save is still there, clicked not pressed by Enter
