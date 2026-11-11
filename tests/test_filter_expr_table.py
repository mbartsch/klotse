import pytest
from PySide6.QtWidgets import QApplication

from klotse.k8s.models import PodRow
from klotse.ui.overview import POD_COLUMNS
from klotse.ui.resource_table import ResourceTable


def pod(name, ns="shop", status="Running", restarts=0, age="5m", node="n1", cpu_req=0.1, cpu_lim=0.1, mem_req=64, mem_lim=64, labels=None):
    raw = {"metadata": {"name": name, "namespace": ns, "uid": "u-" + name, "labels": labels or {}}, "spec": {"containers": [
        {"name": "c", "resources": {"requests": {"cpu": f"{int(cpu_req * 1000)}m", "memory": f"{mem_req}Mi"},
                                    "limits": {"cpu": f"{int(cpu_lim * 1000)}m", "memory": f"{mem_lim}Mi"}}}]}, "status": {"phase": "Running"}}
    return PodRow(name, ns, "1/1", status, restarts, node, age, raw)


ROWS = [pod("web-1", restarts=0, cpu_lim=0.2, labels={"app": "web"}),
        pod("web-2", restarts=5, status="CrashLoopBackOff", age="3h", cpu_lim=0.1),
        pod("db-1", ns="data", restarts=2, cpu_req=0.5, cpu_lim=1.0, mem_req=512, mem_lim=2048, age="2d", node="n2", labels={"app": "db"}),
        pod("kube-dns", ns="kube-system", restarts=1, age="30d")]


@pytest.fixture
def table(qtbot):
    t = ResourceTable(POD_COLUMNS, "Pod", customizable=True)
    qtbot.addWidget(t)
    t.set_rows(ROWS)
    return t


def test_filter_and_resource_list_have_accessible_names(table):
    assert table.filter.accessibleName() == "Filter Pod list"
    assert table.view.accessibleName() == "Pod list"


def test_filter_completions_include_columns_labels_and_nested_manifest_paths(table):
    candidates = set(table._completion_model.stringList())
    assert {"restarts", "status.phase", "spec.containers.0.resources.requests.cpu", "app", "label.app"} <= candidates


def test_completing_a_word_preserves_the_rest_of_the_filter_expression(table, qtbot):
    table.filter.setText("status.ph = Running")
    table.filter.setCursorPosition(len("status.ph"))
    table._completer.setCompletionPrefix("status.ph")
    matches = [table._completion_model.data(table._completion_model.index(i, 0))
               for i in range(table._completion_model.rowCount())
               if table._completion_model.data(table._completion_model.index(i, 0)).casefold().startswith("status.ph")]
    assert "status.phase" in matches
    table._completer.activated.emit("status.phase")
    assert table.filter.text() == "status.phase = Running"


def shown(table, qtbot, text):
    table.filter.setText(text)
    qtbot.waitUntil(lambda: not table._filter_timer.isActive(), timeout=2000)
    return sorted(table.proxy.index(i, 0).data() for i in range(table.proxy.rowCount()))


def test_plain_text_still_matches_any_column(table, qtbot):
    assert shown(table, qtbot, "web") == ["web-1", "web-2"]
    assert shown(table, qtbot, "crashloop") == ["web-2"]
    assert shown(table, qtbot, "") == ["db-1", "kube-dns", "web-1", "web-2"]
    assert not table.filter_note.isVisibleTo(table)


def test_numbers_and_units(table, qtbot):
    assert shown(table, qtbot, "Restarts != 0") == ["db-1", "kube-dns", "web-2"]
    assert shown(table, qtbot, "Restarts > 3") == ["web-2"]
    assert shown(table, qtbot, "Age > 1d") == ["db-1", "kube-dns"]
    assert shown(table, qtbot, "Age < 1h") == ["web-1"]
    assert shown(table, qtbot, "MemLim >= 1Gi") == ["db-1"]
    assert shown(table, qtbot, "CPULim > 500m") == ["db-1"]


def test_columns_against_columns_and_arithmetic(table, qtbot):
    assert shown(table, qtbot, "CPULim = CPUReq") == ["kube-dns", "web-2"]
    assert shown(table, qtbot, "CPULim = 2*CPUReq") == ["db-1", "web-1"]
    assert shown(table, qtbot, "MemLim = MemReq*4") == ["db-1"]
    assert shown(table, qtbot, "CPULim != CPUReq") == ["db-1", "web-1"]


def test_text_columns_labels_kind_and_logic(table, qtbot):
    assert shown(table, qtbot, "Name = web-*") == ["web-1", "web-2"]
    assert shown(table, qtbot, "Name ~ '^(db|kube)-'") == ["db-1", "kube-dns"]
    assert shown(table, qtbot, "Namespace != kube-system and Restarts > 0") == ["db-1", "web-2"]
    assert shown(table, qtbot, "Status = crashloopbackoff or Node = n2") == ["db-1", "web-2"]
    assert shown(table, qtbot, "app = web") == ["web-1"] and shown(table, qtbot, "label.app = db") == ["db-1"]
    assert shown(table, qtbot, "Kind = Pod") == ["db-1", "kube-dns", "web-1", "web-2"]
    assert shown(table, qtbot, "(Restarts > 3 or Age > 20d) and Namespace != data") == ["kube-dns", "web-2"]


def test_a_bad_expression_is_explained_and_filters_nothing(table, qtbot):
    assert shown(table, qtbot, "Restarts >") == ["db-1", "kube-dns", "web-1", "web-2"]
    assert table.filter_note.isVisibleTo(table) and "Filter:" in table.filter_note.text()
    assert shown(table, qtbot, "Age > 5") == ["db-1", "kube-dns", "web-1", "web-2"] and "needs a unit" in table.filter_note.text()
    assert shown(table, qtbot, "Restarts > 3") == ["web-2"] and not table.filter_note.isVisibleTo(table)  # fixed: the note goes


def test_an_expression_applies_to_rows_added_later(table, qtbot):
    shown(table, qtbot, "Restarts > 3")
    table.append_rows([pod("late", restarts=9), pod("quiet", restarts=0)])
    assert sorted(table.proxy.index(i, 0).data() for i in range(table.proxy.rowCount())) == ["late", "web-2"]


def test_blank_usage_matches_nothing(table, qtbot):
    assert shown(table, qtbot, "CPU > 0") == []  # no metrics in these rows
    ROWS_WITH = [pod("hot")]
    ROWS_WITH[0].cpu_used = 0.9
    table.set_rows(ROWS_WITH + ROWS)
    assert shown(table, qtbot, "CPU > 500m") == ["hot"]


@pytest.fixture
def manifest_table(table):
    rows = [pod("large"), pod("small"), pod("missing")]
    rows[0].raw.update(spec={"replicas": 12, "suspend": True, "score": -2.5,
                            "template": {"spec": {"containers": [{"image": "nginx:1.27"}]}},
                            "cpu": "500m", "memory": "512Mi", "age": "5m"},
                       status={"phase": "Running", "readyReplicas": 10})
    rows[1].raw.update(spec={"replicas": 2, "suspend": False, "score": 0},
                       status={"phase": "Pending", "readyReplicas": 2})
    rows[2].raw.update(spec={}, status={})
    table.set_rows(rows)
    return table


@pytest.mark.parametrize("expression, expected", [
    ("spec.replicas >= 3", ["large"]),
    ("spec.replicas >= 2", ["large", "small"]),
    ("spec.replicas > status.readyReplicas", ["large"]),
    ("spec.replicas = status.readyReplicas", ["small"]),
    ("spec.replicas / 2 > status.readyReplicas / 2", ["large"]),
    ("status.phase = running", ["large"]),
    ("status.phase ~ '^pend'", ["small"]),
    ("spec.suspend = true", ["large"]),
    ("spec.suspend = 'FALSE'", ["small"]),
    ("spec.suspend != true", ["small"]),
    ("spec.suspend = 1", []),
    ("spec.score < 0", ["large"]),
    ("spec.score = 0", ["small"]),
    ("spec.template.spec.containers.0.image = 'nginx:*'", ["large"]),
    ("spec.cpu = '500m' and spec.memory = '512Mi' and spec.age = '5m'", ["large"]),
    ("status.Phase = Running", []),
    ("status.phase = Running and Name = large", ["large"]),
    ("status.phase = status.absent", []),
])
def test_manifest_scalar_expressions(manifest_table, qtbot, expression, expected):
    assert shown(manifest_table, qtbot, expression) == expected
    assert not manifest_table.filter_note.isVisibleTo(manifest_table)


@pytest.mark.parametrize("path", [
    "spec.absent", "spec.null", "spec.empty", "spec.object", "spec.items",
    "spec.items.0", "spec.items.-1", "spec.items.999999", "spec.items." + "9" * 5000,
    "spec.items.nope", "spec..replicas", "spec.replicas.child",
    "spec.template.spec.containers[0].image", "spec.template.spec.containers.*.image",
])
def test_missing_or_non_scalar_paths_never_match(manifest_table, path):
    from klotse.ui.filter_expr import parse

    row = manifest_table.model.rows[0]
    row.raw["spec"].update(null=None, empty="", object={"key": "value"}, items=[])
    for operator in ("=", "!=", ">", "~", "!~"):
        expression = parse(f"{path} {operator} 'anything'")
        assert not expression.matches(lambda name: manifest_table._resolve(row, name))
    # An absent path on the right is a known missing value, never literal text.
    row.raw["status"]["phase"] = path
    assert not parse(f"status.phase = {path}").matches(lambda name: manifest_table._resolve(row, name))


def test_manifest_paths_take_precedence_and_have_no_column_units(manifest_table, qtbot):
    manifest_table.model.columns = [("Name", "name"), ("spec.cpu", "cpu_req"), ("status.phase", "status")]
    row = manifest_table.model.rows[0]
    row.raw["metadata"]["labels"]["spec.cpu"] = "label value"
    assert manifest_table._unit_of("spec.cpu") == ""
    assert manifest_table._resolve(row, "spec.cpu") == ("500m", None)
    assert shown(manifest_table, qtbot, "spec.cpu = '500m'") == ["large"]
    assert shown(manifest_table, qtbot, "label.spec.cpu = 'label value'") == ["large"]
    shown(manifest_table, qtbot, "spec.cpu > 500m")
    assert "plain number" in manifest_table.filter_note.text()


def test_manifest_filter_applies_after_append_and_refresh(manifest_table, qtbot):
    assert shown(manifest_table, qtbot, "spec.replicas >= 3") == ["large"]
    late = pod("late")
    late.raw["spec"]["replicas"] = 4
    manifest_table.append_rows([late])
    assert sorted(manifest_table.proxy.index(i, 0).data() for i in range(manifest_table.proxy.rowCount())) == ["large", "late"]
    replacement = pod("large")
    replacement.raw["spec"]["replicas"] = 1
    manifest_table.set_rows([replacement, late])
    assert manifest_table.proxy.rowCount() == 1
    assert manifest_table.proxy.index(0, 0).data() == "late"


@pytest.mark.parametrize("raw", [None, {}, [], "invalid", {"spec": None}])
def test_manifest_paths_tolerate_absent_or_malformed_raw(table, raw):
    row = pod("empty")
    row.raw = raw
    assert table._resolve(row, "spec.replicas") == (None, None)


def test_status_shows_filtered_and_total_row_counts(table, qtbot):
    assert table.status.text().startswith("4 rows")

    assert shown(table, qtbot, "web") == ["web-1", "web-2"]
    assert table.status.text().startswith("Shown: 2, total: 4 rows")

    assert shown(table, qtbot, "no-match") == []
    assert table.status.text().startswith("Shown: 0, total: 4 rows")

    assert shown(table, qtbot, "") == ["db-1", "kube-dns", "web-1", "web-2"]
    assert table.status.text().startswith("4 rows")


def test_completion_restores_expression_if_line_edit_replaces_text(table, qtbot):
    expression = "Restarts > re"
    table.filter.setText(expression)
    table.filter.setCursorPosition(len(expression))
    table._show_filter_completions(expression)

    # Simulate QLineEdit replacing the whole edit before QCompleter's activated
    # signal reaches the custom insertion handler.
    table.filter.setText("restarts")
    table._completer.activated.emit("restarts")

    assert table.filter.text() == "Restarts > restarts"
