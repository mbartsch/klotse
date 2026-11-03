from types import SimpleNamespace

import pytest
import yaml

from klotse.ui.detail_panel import DetailPanel
from klotse.ui.edit_dialog import EditDialog
from klotse.ui.manifest import dump_manifest


@pytest.mark.parametrize("value", [
    "server:\n  port: 8080",
    "server:\n  port: 8080\n",
    "server:\n  port: 8080\n\n",
    "  indented\ntext\n",
    "# café\nvalue: on\n",
    "line with trailing spaces  \nnext\n",
    "tab\there\nnext\n",
    "true", "123", "", '{"port":8080}',
])
def test_manifest_preserves_configmap_values(value):
    body = {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": "config", "resourceVersion": "123"},
            "data": {"app.yaml": value}}
    assert yaml.safe_load(dump_manifest(body)) == body


def test_configmap_display_and_edit(qtbot):
    body = {"kind": "ConfigMap", "metadata": {"name": "config"},
            "data": {"app.yaml": "server:\n  port: 8080\n"}}
    panel = DetailPanel()
    dialog = EditDialog("Edit ConfigMap", body)
    qtbot.addWidget(panel)
    qtbot.addWidget(dialog)
    panel.show_row(SimpleNamespace(raw=body))
    assert "app.yaml: |\n    server:\n      port: 8080\n" in panel.toPlainText()
    assert dialog.editor.toPlainText() == panel.toPlainText()
    dialog.editor.setPlainText(dialog.editor.toPlainText().replace("port: 8080", "port: 9090"))
    dialog._save()
    assert dialog.result_body["data"]["app.yaml"] == "server:\n  port: 9090\n"
