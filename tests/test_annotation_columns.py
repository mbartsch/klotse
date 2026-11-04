"""Annotation columns next to label columns (RBAC lists): settings file, table model, table and Columns… dialog."""
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.resources import SPEC_BY_KEY, Row
from klotse.ui.customizations import ANNOTATION_KINDS, LabelColumnsDialog, LabelSettings, parse_label_columns
from klotse.ui.resource_table import ResourceTable
from klotse.ui.table_model import ANNOTATION_CELL_LIMIT, ANNOTATION_TIP_LIMIT

LONG = "x" * 500


def store(tmp_path):
    return QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)


def role_row(name, annotations=None, labels=None):
    meta = {"name": name, "annotations": annotations or {}, "labels": labels or {}}
    return Row(name, "dev", {"metadata": meta}, rbac_kind="Role", rule_count=1, rules="x", age="1d")


def role_table(qtbot):
    spec = SPEC_BY_KEY["roles"]
    table = ResourceTable(spec.columns, spec.kind)
    qtbot.addWidget(table)
    return table


# ---- settings ---------------------------------------------------------------------
def test_annotations_are_saved_readably_next_to_the_labels_and_reloaded(tmp_path):
    settings = LabelSettings(store(tmp_path))
    assert settings.annotations("Role") == [] and settings.annotation_headers("Role") == {}
    settings.set_labels("Role", ["app"])
    settings.set_annotations("Role", ["example.com/owner", "note"], {"example.com/owner": "Owner"})
    text = (tmp_path / "k.ini").read_text(encoding="utf-8")
    assert "[columns.Role]" in text and "annotations.0=example.com/owner" in text
    assert "annotations_name.0=Owner" in text and "labels.0=app" in text
    again = LabelSettings(store(tmp_path))
    assert again.annotations("Role") == ["example.com/owner", "note"]
    assert again.annotation_headers("Role") == {"example.com/owner": "Owner"} and again.labels("Role") == ["app"]


def test_changing_one_list_keeps_the_other_and_other_kinds(tmp_path):
    settings = LabelSettings(store(tmp_path))
    settings.set_annotations("Role", ["a"])
    settings.set_annotations("ClusterRole", ["b"])
    settings.set_labels("Role", ["l"])
    settings.set_layout("Role", ["name"], ["age"])
    again = LabelSettings(store(tmp_path))
    assert (again.annotations("Role"), again.annotations("ClusterRole"), again.labels("Role")) == (["a"], ["b"], ["l"])
    assert again.layout("Role")["hidden"] == ["age"]
    again.set_annotations("Role", [])  # an empty list means "none" and is kept
    assert LabelSettings(store(tmp_path)).annotations("Role") == []


def test_annotation_keys_are_validated_with_annotation_wording(tmp_path):
    settings = LabelSettings(store(tmp_path))
    with pytest.raises(ValueError, match="annotation keys"):
        settings.set_annotations("Role", ["key=value"])
    with pytest.raises(ValueError, match="annotation key before"):
        parse_label_columns(["= Heading"], "annotation")
    with pytest.raises(ValueError, match="single-line"):
        settings.set_annotations("Role", ["a"], {"a": "two\nlines"})
    assert settings.annotations("Role") == []  # nothing was saved by the failed calls


def test_a_bad_annotation_list_in_the_file_is_ignored(tmp_path):
    raw = store(tmp_path)
    raw.setValue("columns.Role/annotations.0", "has space")
    raw.setValue("columns.Role/labels.0", "app")
    raw.sync()
    settings = LabelSettings(store(tmp_path))
    assert settings.annotations("Role") == [] and settings.labels("Role") == ["app"]


# ---- the table --------------------------------------------------------------------
def test_annotation_columns_follow_the_labels_and_show_values(qtbot):
    table = role_table(qtbot)
    table.set_label_columns(["app"], {"app": "App"}, None, ["example.com/owner"], {"example.com/owner": "Owner"})
    attrs = [c[1] for c in table.model.columns]
    assert attrs[:4] == ["name", "label:app", "annotation:example.com/owner", "namespace"]
    assert [c[0] for c in table.model.columns][1:3] == ["App", "Owner"]
    table.set_rows([role_row("a", {"example.com/owner": "team-a"}, {"app": "x"}), role_row("b")])
    col = attrs.index("annotation:example.com/owner")
    assert [table.model.index(r, col).data() for r in (0, 1)] == ["team-a", ""]  # a missing annotation is blank
    assert table.model.headerData(col, Qt.Horizontal, Qt.ToolTipRole) == "example.com/owner"
    assert table.model.headerData(1, Qt.Horizontal, Qt.ToolTipRole) == "app"  # labels are unchanged


def test_annotation_cells_are_one_short_line_and_the_tooltip_has_more(qtbot):
    table = role_table(qtbot)
    table.set_label_columns([], None, None, ["n"])
    table.set_rows([role_row("a", {"n": "line one\n  line   two"}), role_row("b", {"n": LONG}),
                    role_row("c", {"n": "y" * 5000})])
    col = [c[1] for c in table.model.columns].index("annotation:n")
    cell = lambda r, role=Qt.DisplayRole: table.model.index(r, col).data(role)
    assert cell(0) == "line one line two" and cell(0, Qt.ToolTipRole) == "line one\n  line   two"
    assert len(cell(1)) == ANNOTATION_CELL_LIMIT and cell(1).endswith("…")
    assert cell(1, Qt.ToolTipRole) == LONG
    assert len(cell(2, Qt.ToolTipRole)) == ANNOTATION_TIP_LIMIT + 1
    assert cell(1, Qt.EditRole) == LONG  # sorting uses the real value


def test_annotation_columns_are_filterable_and_sortable(qtbot):
    table = role_table(qtbot)
    table.set_label_columns([], None, None, ["n"])
    table.set_rows([role_row("a", {"n": "zebra"}), role_row("b", {"n": "apple"}), role_row("c")])
    col = [c[1] for c in table.model.columns].index("annotation:n")
    table.view.sortByColumn(col, Qt.AscendingOrder)
    assert [r.name for r in (table.model.rows[table.proxy.mapToSource(table.proxy.index(i, 0)).row()]
                             for i in range(3))] == ["c", "b", "a"]
    table.filter.setText("zebra")
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 1, timeout=2000)


def test_sort_and_columns_survive_reapplying_with_annotations(qtbot):
    table = role_table(qtbot)
    table.set_label_columns([], None, None, ["n"])
    col = [c[1] for c in table.model.columns].index("annotation:n")
    table.view.sortByColumn(col, Qt.DescendingOrder)
    table.set_label_columns(["app"], None, None, ["n"])  # a label column appears before it: the sort follows the attribute
    new = [c[1] for c in table.model.columns].index("annotation:n")
    assert table.view.horizontalHeader().sortIndicatorSection() == new
    table.set_label_columns([], None, None, [])  # removing the sorted column falls back to unsorted
    assert not any(c[1].startswith("annotation:") for c in table.model.columns)


def test_old_callers_without_annotations_still_work(qtbot):
    table = role_table(qtbot)
    table.set_label_columns(["app"], None, {"hidden": []})
    assert [c[1] for c in table.model.columns][1] == "label:app"
    table.set_extra_columns([])  # re-applies the remembered arguments, annotations included
    table.set_label_columns(["app"], None, None, ["n"])
    table.set_extra_columns([("X", "plugin:x:y")])
    assert "annotation:n" in [c[1] for c in table.model.columns]


def test_columns_button_mentions_annotations_only_for_rbac_kinds(qtbot):
    assert "annotation" in role_table(qtbot).customize_button.toolTip()
    other = ResourceTable(SPEC_BY_KEY["configmaps"].columns, "ConfigMap")
    qtbot.addWidget(other)
    assert "annotation" not in other.customize_button.toolTip()
    assert {"Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "ServiceAccount"} == set(ANNOTATION_KINDS)


# ---- the dialog -------------------------------------------------------------------
def test_dialog_has_an_annotation_section_for_rbac_kinds_only(qtbot, tmp_path):
    settings = LabelSettings(store(tmp_path))
    settings.set_annotations("Role", ["example.com/owner"], {"example.com/owner": "Owner"})
    dlg = LabelColumnsDialog("Role", settings, ["app"], (), None, available_annotations=["a/one", "b/two"])
    qtbot.addWidget(dlg)
    assert dlg.annotation_editor.toPlainText() == "example.com/owner = Owner"
    assert [dlg.annotation_available.itemText(i) for i in range(dlg.annotation_available.count())] == ["a/one", "b/two"]
    assert [dlg.available.itemText(i) for i in range(dlg.available.count())] == ["app"]  # labels keep their own list
    plain = LabelColumnsDialog("Pod", settings, ["app"])
    qtbot.addWidget(plain)
    assert plain.annotation_editor is None
    plain._add_annotation()  # nothing to do, and no error


def test_dialog_adds_and_saves_annotation_columns_without_touching_labels(qtbot, tmp_path):
    settings = LabelSettings(store(tmp_path))
    settings.set_labels("Role", ["app"])
    dlg = LabelColumnsDialog("Role", settings, (), (), None, available_annotations=["example.com/owner"])
    qtbot.addWidget(dlg)
    dlg.annotation_available.setCurrentText("example.com/owner")
    dlg.annotation_heading.setText("Owner")
    dlg._add_annotation()
    dlg.annotation_heading.setText("")
    dlg._add_annotation()  # adding the same key again keeps its heading and does not duplicate it
    assert dlg.annotation_editor.toPlainText() == "example.com/owner = Owner"
    dlg._save()
    assert settings.annotations("Role") == ["example.com/owner"] and settings.labels("Role") == ["app"]
    assert settings.annotation_headers("Role") == {"example.com/owner": "Owner"}


def test_dialog_saves_nothing_when_the_annotation_list_is_invalid(qtbot, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    settings = LabelSettings(store(tmp_path))
    settings.set_labels("Role", ["app"])
    dlg = LabelColumnsDialog("Role", settings)
    qtbot.addWidget(dlg)
    dlg.editor.setPlainText("team")  # a valid label change ...
    dlg.annotation_editor.setPlainText("bad key")  # ... with an invalid annotation list
    dlg._save()
    assert warned and "annotation keys" in warned[0]
    assert settings.labels("Role") == ["app"] and settings.annotations("Role") == []  # neither half was saved


def test_restore_defaults_clears_annotation_columns_too(qtbot, tmp_path):
    settings = LabelSettings(store(tmp_path))
    settings.set_annotations("Role", ["a"])
    dlg = LabelColumnsDialog("Role", settings)
    qtbot.addWidget(dlg)
    dlg._restore()
    assert dlg.annotation_editor.toPlainText() == ""


def test_workspace_applies_annotation_columns_to_open_rbac_tables(qtbot, tmp_path):
    from types import SimpleNamespace
    from klotse.ui.workspace import ClusterWorkspace
    settings = LabelSettings(store(tmp_path))
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", label_settings=settings)
    qtbot.addWidget(ws)
    settings.set_annotations("ClusterRole", ["example.com/owner"], {"example.com/owner": "Owner"})
    table = ws.views["clusterroles"][0]
    assert "annotation:example.com/owner" in [c[1] for c in table.model.columns]
    assert "annotation:example.com/owner" not in [c[1] for c in ws.views["roles"][0].model.columns]  # per kind
