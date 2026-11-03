from datetime import datetime, timezone

import pytest
import yaml
from kubernetes import client
from PySide6.QtCore import Qt

from klotse.k8s.resources import Row, map_secret
from klotse.ui.detail_panel import DetailPanel


def secret_row(name="credentials", data=None):
    return map_secret(client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace="ns", creation_timestamp=datetime.now(timezone.utc)),
        data=data if data is not None else {"password": "c2VjcmV0", "user": "YWRtaW4="},
    ))


def test_decode_multiple_keys_in_yaml_and_hide(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    row = secret_row(data={"password": "c2VjcmV0", "user": "YWRtaW4=", "token": "dG9rZW4="})
    panel.show_row(row)
    assert not panel.decode_selected_button.isEnabled()
    assert yaml.safe_load(panel.toPlainText())["data"] == dict.fromkeys(row.secret_data, "<hidden>")
    for i in (0, 2):  # password and user, leaving token hidden
        panel.decode_list.item(i).setCheckState(Qt.Checked)
    assert panel.decode_selected_button.text() == "Decode selected (2)"
    panel.decode_selected_button.click()
    assert yaml.safe_load(panel.toPlainText())["data"] == {
        "password": "secret", "user": "admin", "token": "<hidden>"}
    assert row.raw["data"] == dict.fromkeys(row.secret_data, "<hidden>")
    panel.hide_values_button.click()
    assert yaml.safe_load(panel.toPlainText())["data"] == row.raw["data"]
    assert not panel.decode_selected_button.isEnabled()
    panel.decode_list.item(0).setCheckState(Qt.Checked)
    panel._decode_selected()
    panel.show_row(secret_row("other", {"other-key": "dG9rZW4="}))
    assert "secret" not in panel.toPlainText()
    assert not panel.decode_selected_button.isEnabled()
    assert panel.decode_list.count() == 1
    panel.show_row(Row("config", "ns", {"kind": "ConfigMap"}))
    assert panel.decode_button.isHidden()
    assert panel.hide_values_button.isHidden()
    assert panel.decode_list.count() == 0


@pytest.mark.parametrize("encoded, expected", [
    ("bm90ZXM6CsOpCg==", "notes:\né\n"),
    ("", ""),
    ("/wA=", "Binary value (hex):\nff 00"),
    ("invalid!", "Unable to decode this value: invalid base64."),
])
def test_decode_value_formats(qtbot, encoded, expected):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show_row(secret_row(data={"value": encoded}))
    panel.decode_list.item(0).setCheckState(Qt.Checked)
    panel._decode_selected()
    assert yaml.safe_load(panel.toPlainText())["data"]["value"] == expected


def test_scrollable_picker_preserves_checks_across_filters(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    row = secret_row(data={f"key-{i:03}": "c2VjcmV0" for i in range(200)})
    panel.show_row(row)
    panel.show()
    panel.decode_menu.popup(panel.decode_button.mapToGlobal(panel.decode_button.rect().bottomLeft()))
    qtbot.waitUntil(panel.decode_menu.isVisible)
    assert panel.decode_list.verticalScrollBar().maximum() > 0
    panel.decode_list.item(0).setCheckState(Qt.Checked)
    panel.decode_filter.setText("KEY-19")
    visible = [panel.decode_list.item(i).text() for i in range(200)
               if not panel.decode_list.item(i).isHidden()]
    assert visible == [f"key-{i}" for i in range(190, 200)]
    assert panel.decode_count.text() == "10 of 200 keys"
    panel.decode_list.item(190).setCheckState(Qt.Checked)
    assert panel.decode_selected_button.text() == "Decode selected (2)"
    assert yaml.safe_load(panel.toPlainText())["data"] == row.raw["data"]
    panel.decode_selected_button.click()
    values = yaml.safe_load(panel.toPlainText())["data"]
    assert {key for key, value in values.items() if value != "<hidden>"} == {"key-000", "key-190"}
    assert not panel.decode_menu.isVisible()
    panel.decode_filter.setText("missing")
    assert panel.decode_count.text() == "No matching keys"
    panel._open_picker()
    assert panel.decode_filter.text() == ""
    assert panel.decode_count.text() == "200 of 200 keys"
    assert panel._checked_keys() == {"key-000", "key-190"}
