from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog

from klotse.ui.customizations import LabelSettings
from klotse.ui.resource_table import ResourceTable
from klotse.ui.saved_filters import SavedFilters
from klotse.ui.workspace import ClusterWorkspace


def store(tmp_path):
    return QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)


def test_filters_are_kept_per_cluster_and_kind_and_a_name_replaces(tmp_path):
    saved = SavedFilters(store(tmp_path))
    saved.add("a", "Pod", "crashing", "CrashLoop")
    saved.add("a", "Pod", "crashing", "Error")
    saved.add("a", "Node", "spot", "spot")
    saved.add("b", "Pod", "other", "x")
    again = SavedFilters(store(tmp_path))
    assert [e["text"] for e in again.for_list("a", "Pod")] == ["Error"]
    assert [e["name"] for e in again.for_list("a", "Node")] == ["spot"] and again.for_list("c", "Pod") == []
    again.remove(again.for_list("a", "Pod")[0])
    assert again.for_list("a", "Pod") == []


def test_empty_names_or_texts_are_refused(tmp_path):
    saved = SavedFilters(store(tmp_path))
    for name, text in (("", "x"), ("n", " ")):
        with pytest.raises(ValueError):
            saved.add("a", "Pod", name, text)
    assert saved.all() == []


def test_table_menu_saves_applies_and_forgets(qtbot, tmp_path, monkeypatch):
    saved = SavedFilters(store(tmp_path))
    table = ResourceTable([("Name", "name")], "Pod")
    qtbot.addWidget(table)
    table.enable_saved_filters(saved, "ctx")
    table.enable_saved_filters(saved, "ctx")  # a second call changes nothing
    table._fill_saved_filters()
    assert table.saved_menu.actions()[0].text() == "Save current filter as…" and not table.saved_menu.actions()[0].isEnabled()
    table.filter.setText("CrashLoop")
    table._fill_saved_filters()
    assert table.saved_menu.actions()[0].isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("crashing", True)))
    table._save_filter()
    assert [e["name"] for e in saved.for_list("ctx", "Pod")] == ["crashing"]
    table.filter.setText("")
    table._fill_saved_filters()
    texts = [a.text() for a in table.saved_menu.actions() if not a.isSeparator()]
    assert "crashing: CrashLoop" in texts
    next(a for a in table.saved_menu.actions() if a.text() == "crashing: CrashLoop").trigger()
    assert table.filter.text() == "CrashLoop"
    forget = next(a for a in table.saved_menu.actions() if a.text() == "Forget").menu()
    forget.actions()[0].trigger()
    assert saved.for_list("ctx", "Pod") == []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    table._save_filter()  # no name: nothing saved
    assert saved.all() == []


def test_workspace_lists_get_the_button_with_their_own_cluster(qtbot, tmp_path):
    ws = ClusterWorkspace(SimpleNamespace(), "prod", label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(ws)
    assert ws.pod_table.saved_button is not None and ws.pod_table._saved_context == "prod"
    assert ws.node_table.saved_button is not None
