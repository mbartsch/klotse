from types import SimpleNamespace

from klotse.ui.detail_panel import DetailPanel


def test_metadata_disclosure_tracks_selection_and_summary(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    row = SimpleNamespace(raw={"kind": "Pod", "metadata": {"name": "pod", "labels": {"team": "a"},
                           "annotations": {"owner": "x", "kubectl.kubernetes.io/last-applied-configuration": "big"}},
                           "spec": {}, "status": {}}, name="pod", table_kind="Pod")
    panel.show_row(row)
    labels, label_values = panel.metadata_controls["Labels"]
    annotations, _ = panel.metadata_controls["Annotations"]
    assert not labels.isHidden() and labels.text() == "Labels (1)"
    assert annotations.text() == "Annotations (1)"
    assert label_values.isHidden()
    labels.click()
    assert not label_values.isHidden() and "team = a" in label_values.toPlainText()
    panel.yaml_button.click()
    assert panel.metadata_area.isHidden()
    panel.show_row(SimpleNamespace(raw={"kind": "Pod", "metadata": {"name": "empty"}, "spec": {}, "status": {}},
                                   name="empty", table_kind="Pod"))
    panel.formatted_button.click()
    assert panel.metadata_area.isHidden()
