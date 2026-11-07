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
