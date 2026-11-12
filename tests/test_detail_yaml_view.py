"""Details pane: the YAML view must stay reachable for every kind, also in a narrow inspector (the view buttons used to shrink to zero width)."""
import pytest
from PySide6.QtWidgets import QScrollArea

from klotse.ui.detail_panel import DetailPanel


def _row(kind, api="v1", spec=None):
    raw = {"apiVersion": api, "kind": kind, "metadata": {"name": "x", "namespace": "n"}, "spec": spec or {}, "status": {}}
    row = type("Row", (), {})()
    row.raw, row.name, row.namespace, row.table_kind, row.extra = raw, "x", "n", kind, {}
    return row


@pytest.mark.parametrize("kind, api", [("PersistentVolumeClaim", "v1"), ("HorizontalPodAutoscaler", "autoscaling/v2"),
                                       ("Widget", "example.com/v1"), ("Deployment", "apps/v1")])
def test_yaml_is_one_click_away_for_every_kind(qtbot, kind, api):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(_row(kind, api))
    assert panel.view_scroll.isVisible() and panel.yaml_button.isVisible() and panel.yaml_button.isEnabled()
    panel.yaml_button.click()
    assert panel.yaml_button.isChecked() and panel.editor.isVisible()
    assert panel.editor.toPlainText().startswith(f"apiVersion: {api}\nkind: {kind}")


def test_view_buttons_keep_their_labels_in_a_narrow_inspector(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.resize(300, 600)
    panel.show()
    panel.show_row(_row("PersistentVolumeClaim"))
    for button in (panel.formatted_button, panel.yaml_button, panel.events_button, panel.related_button):
        assert button.width() >= button.sizeHint().width() > 0, button.text()
    scroll = panel.view_scroll
    assert isinstance(scroll, QScrollArea) and scroll.widget() is panel.view_bar
    assert scroll.horizontalScrollBar().maximum() > 0  # narrow: the bar scrolls instead of squeezing labels


def test_the_view_bar_is_hidden_when_nothing_is_selected(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(_row("ConfigMap"))
    assert panel.view_scroll.isVisible()
    panel.clear_row()
    assert not panel.view_scroll.isVisible()
