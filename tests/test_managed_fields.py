import base64
from types import SimpleNamespace as NS

from klotse.ui.detail_panel import DetailPanel
from klotse.ui.main_window import MainWindow

from test_app import FakeService, open_active

RAW = {"apiVersion": "v1", "kind": "Pod",
       "metadata": {"name": "web", "managedFields": [{"manager": "kubectl-client-side-apply", "operation": "Update"}]},
       "spec": {}}


def test_managed_fields_hidden_by_default_and_toggle_redraws(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(NS(raw=RAW))
    assert "managedFields" not in panel.toPlainText() and "name: web" in panel.toPlainText()
    panel.set_show_managed_fields(True)
    assert "managedFields" in panel.toPlainText() and "kubectl-client-side-apply" in panel.toPlainText()
    panel.set_show_managed_fields(False)
    assert "managedFields" not in panel.toPlainText()
    assert "managedFields" in RAW["metadata"]  # the row's own manifest is never modified


def test_manifests_without_metadata_or_managed_fields_are_unchanged(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    for raw in ({}, {"kind": "X"}, {"metadata": {"name": "a"}}):
        panel.show_row(NS(raw=raw))
    assert "name: a" in panel.toPlainText()
    panel.set_show_managed_fields(True)  # toggling with a row shown is harmless
    panel.set_show_managed_fields(False)


def test_toggle_keeps_decoded_secret_values(qtbot):
    secret = {"kind": "Secret", "metadata": {"name": "s", "managedFields": [{"manager": "m"}]}, "data": {"k": "****"}}
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(NS(raw=secret, secret_data={"k": base64.b64encode(b"plain").decode()}))
    panel._decoded_keys = {"k"}
    panel._render_secret()
    assert "plain" in panel.toPlainText() and "managedFields" not in panel.toPlainText()
    panel.set_show_managed_fields(True)
    assert "plain" in panel.toPlainText() and "managedFields" in panel.toPlainText()
    assert secret["data"]["k"] == "****"


def test_view_menu_option_applies_to_open_and_new_workspaces(qtbot):
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    open_active(win)
    action = win.managed_fields_action
    assert action.isCheckable() and not action.isChecked()
    ws = win.workspace("ctx")
    assert not ws.detail.show_managed_fields
    action.setChecked(True)
    assert ws.detail.show_managed_fields
    assert [a.text() for a in win.menuBar().actions()][:3] == ["File", "Edit", "View"]
