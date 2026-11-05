"""A running Pod only accepts added tolerations: the action buttons say so (greyed out) instead of pretending to work."""
from copy import deepcopy

from PySide6.QtCore import Qt

from klotse.ui.workload_editor import WorkloadEditDialog

POD = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "p", "namespace": "n"},
       "spec": {"containers": [{"name": "c", "image": "x:1"}],
                "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "web", "effect": "NoSchedule"}]},
       "status": {"phase": "Running"}}


def make(qtbot, create=False):
    dlg = WorkloadEditDialog("Pod", deepcopy(POD), namespaces=["n"], create=create)
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


def test_existing_tolerations_of_a_running_pod_are_read_only_and_cannot_be_removed(qtbot):
    dlg = make(qtbot)
    tol = dlg.pod.tolerations
    assert not tol.table.item(0, 0).flags() & Qt.ItemIsEditable
    assert not tol.remove_button.isEnabled()  # nothing selected
    tol.table.selectRow(0)
    assert not tol.remove_button.isEnabled() and "only accepts added" in tol.remove_button.toolTip()
    tol.remove_button.click()  # even if it were clicked, the row stays
    assert tol.table.rowCount() == 1


def test_an_added_toleration_can_be_edited_and_removed(qtbot):
    dlg = make(qtbot)
    tol = dlg.pod.tolerations
    assert tol.add_button.isEnabled()
    tol.add_button.click()
    assert tol.table.rowCount() == 2 and tol.table.item(1, 0).flags() & Qt.ItemIsEditable
    tol.table.item(1, 0).setText("extra")
    tol.table.selectRow(1)
    assert tol.remove_button.isEnabled()
    dlg._save()
    got = dlg.result_body["spec"]["tolerations"]
    assert got[0] == POD["spec"]["tolerations"][0] and got[1]["key"] == "extra"
    tol.remove_button.click()
    assert tol.table.rowCount() == 1


def test_selecting_an_existing_and_an_added_row_together_keeps_remove_off(qtbot):
    dlg = make(qtbot)
    tol = dlg.pod.tolerations
    tol.add_button.click()
    tol.table.selectAll()
    assert not tol.remove_button.isEnabled()


def test_a_new_pod_has_no_locked_rows(qtbot):
    dlg = make(qtbot, create=True)
    tol = dlg.pod.tolerations
    assert tol.table.item(0, 0).flags() & Qt.ItemIsEditable
    tol.table.selectRow(0)
    assert tol.remove_button.isEnabled()
