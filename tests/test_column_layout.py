from PySide6.QtCore import QSettings, Qt

from kubeman.ui.customizations import DEFAULT_HIDDEN, LabelColumnsDialog, LabelSettings
from kubeman.ui.overview import POD_COLUMNS
from kubeman.ui.resource_table import ResourceTable


def settings(tmp_path):
    return LabelSettings(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))


def attrs(table):
    return [c[1] for c in table.model.columns]


def test_layout_reorders_and_hides_builtin_columns_but_keeps_name_first(qtbot):
    table = ResourceTable(POD_COLUMNS, "Pod")
    qtbot.addWidget(table)
    base = [c[1] for c in POD_COLUMNS]
    second, third = base[1], base[2]
    table.set_label_columns([], None, {"order": [third, second], "hidden": [base[3], "name"]})
    result = attrs(table)
    assert result[0] == "name"
    assert result.index(third) < result.index(second)
    assert base[3] not in result


def test_labels_follow_name_when_anchor_hidden(qtbot):
    table = ResourceTable(POD_COLUMNS, "Pod")
    qtbot.addWidget(table)
    table.set_label_columns(["app"], None, {"hidden": []})
    assert attrs(table)[1] == "label:app"


def test_other_kinds_can_be_customized_and_persist(qtbot, tmp_path):
    from kubeman.k8s.resources import SPEC_BY_KEY
    spec = SPEC_BY_KEY["daemonsets"]
    store = settings(tmp_path)
    store.set_layout(spec.kind, ["age"], ["namespace"])
    store.set_labels(spec.kind, ["app"], {"app": "App"})
    again = settings(tmp_path)
    assert again.labels(spec.kind) == ["app"] and again.headers(spec.kind) == {"app": "App"}
    table = ResourceTable(spec.columns, spec.kind)
    qtbot.addWidget(table)
    table.set_label_columns(again.labels(spec.kind), again.headers(spec.kind), again.layout(spec.kind))
    result = attrs(table)
    assert result[:2] == ["name", "label:app"] and "namespace" not in result
    assert result.index("age") < len(result) - 1 or result[-1] == "age"
    assert again.labels("Node") == ["karpenter.sh/nodepool"]  # defaults untouched


def test_container_and_custom_resource_tables_are_not_customizable(qtbot):
    for kind in ("Container", "CustomResource"):
        table = ResourceTable(POD_COLUMNS, kind)
        qtbot.addWidget(table)
        assert table.customize_button is None
        before = attrs(table)
        table.set_label_columns(["app"], None, {"hidden": ["ready"]})
        assert attrs(table) == before


def test_layout_persists_and_bad_data_falls_back(tmp_path):
    first = settings(tmp_path)
    first.set_layout("Pod", ["status", "ready"], ["age"])
    assert settings(tmp_path).layout("Pod") == {"order": ["status", "ready"], "hidden": ["age"]}
    bad = QSettings(str(tmp_path / "bad.ini"), QSettings.IniFormat)
    bad.setValue("columns/layout", '{"Pod": {"order": [1]}}')
    assert LabelSettings(bad).layout("Pod") == {"order": [], "hidden": list(DEFAULT_HIDDEN["Pod"])}
    assert LabelSettings(bad).layout("Node") == {"order": [], "hidden": []}


def test_dialog_saves_order_and_hidden(qtbot, tmp_path):
    store = settings(tmp_path)
    dialog = LabelColumnsDialog("Pod", store, (), POD_COLUMNS)
    qtbot.addWidget(dialog)
    dialog.builtin.item(1).setCheckState(Qt.Unchecked)
    dialog.builtin.setCurrentRow(2)
    dialog._move(-1)  # swap rows 1 and 2
    dialog.builtin.setCurrentRow(0)
    dialog._move(1)   # Name is fixed
    dialog._save()
    saved = store.layout("Pod")
    assert saved["order"][0] == "name"
    assert saved["order"][1] == POD_COLUMNS[2][1]
    assert saved["hidden"] == [POD_COLUMNS[1][1]] + [a for a in DEFAULT_HIDDEN["Pod"] if a != POD_COLUMNS[1][1]]
    dialog._restore()
    assert dialog.builtin_layout() == ([c[1] for c in POD_COLUMNS], list(DEFAULT_HIDDEN["Pod"]))
