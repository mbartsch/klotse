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
    assert annotations.text() == "Annotations (2)"
    annotations.click()
    assert "last-applied-configuration" in panel.metadata_controls["Annotations"][1].toPlainText()
    assert label_values.isHidden()
    labels.click()
    assert not label_values.isHidden() and "team = a" in label_values.toPlainText()
    panel.yaml_button.click()
    assert panel.metadata_area.isHidden()
    panel.show_row(SimpleNamespace(raw={"kind": "Pod", "metadata": {"name": "empty"}, "spec": {}, "status": {}},
                                   name="empty", table_kind="Pod"))
    panel.formatted_button.click()
    assert panel.metadata_area.isHidden()


def test_narrow_inspector_scrolls_expanded_metadata_and_summary(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.resize(300, 420)
    row = SimpleNamespace(raw={"kind": "Pod", "metadata": {"name": "a-very-long-resource-name-with-numbers-123456789",
                          "labels": {"team": "a"}, "annotations": {"owner": "b"}}, "spec": {}, "status": {}},
                          name="a-very-long-resource-name-with-numbers-123456789", table_kind="Pod")
    panel.show_row(row)
    panel.show()
    for button, _ in panel.metadata_controls.values():
        button.click()
    qtbot.wait(1)
    assert panel.width() == 300
    assert panel.summary_scroll.verticalScrollBar().maximum() > 0
    assert panel.formatted.minimumHeight() >= 160
    panel.yaml_button.click()
    assert panel.summary_scroll.isHidden()
