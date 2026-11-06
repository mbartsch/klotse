"""Multi-select bulk actions: scale, terminate pods, cordon/delete nodes."""
from types import SimpleNamespace

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QAbstractItemView, QInputDialog, QMenu, QMessageBox

from klotse.k8s.models import DeploymentRow, NodeRow, PodRow
from klotse.ui.workspace import ClusterWorkspace


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def select_all(table):
    table.view.selectAll()
    return table.selected_rows()


def wait_idle(qtbot, ws):
    qtbot.waitUntil(lambda: not ws._workers, timeout=5000)


def test_table_allows_extended_selection_and_lists_selected_rows(qtbot):
    ws = make_ws(qtbot)
    ws.pod_table.set_rows([PodRow(f"p{i}", "ns", "1/1", "Running", 0, "n", "1d", {}) for i in range(3)])
    assert ws.pod_table.view.selectionMode() == QAbstractItemView.ExtendedSelection
    ws.pod_table.view.selectRow(0)
    ws.pod_table.view.selectionModel().select(
        ws.pod_table.proxy.index(2, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    assert sorted(r.name for r in ws.pod_table.selected_rows()) == ["p0", "p2"]


def test_bulk_menu_offers_actions_per_kind(qtbot):
    ws = make_ws(qtbot)
    for table, expected in ((ws.pod_table, ["Terminate 2 pods…", "Force terminate 2 pods…"]),
                            (ws.deploy_table, ["Scale 2 Deployments…", "Restart 2 Deployments…"]),
                            (ws.node_table, ["Cordon 2 nodes…", "Uncordon 2 nodes…", "Delete 2 nodes…", "Force delete 2 nodes…"])):
        menu = QMenu()
        table._bulk_menu(menu, [object(), object()])
        assert [a.text() for a in menu.actions() if not a.isSeparator()] == expected


def test_bulk_scale_applies_one_replica_count_to_every_row(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, scale=lambda *a: calls.append(a), scale_status=lambda *a: {})
    ws.open_scale_monitor = lambda *a: None
    ws.refresh = lambda: None
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("4", True))
    rows = [DeploymentRow(n, "ns", "1/1", 1, 1, "1d", {"spec": {"replicas": 1}}) for n in ("a", "b")]
    ws.bulk_action("Deployment", "scale", rows)
    wait_idle(qtbot, ws)
    assert sorted(calls) == [("Deployment", "a", "ns", 4), ("Deployment", "b", "ns", 4)]


def test_bulk_terminate_confirms_once_and_reports_failures(qtbot, monkeypatch):
    deleted, warnings, questions = [], [], []

    def delete_pod(name, ns, *force):
        if name == "bad":
            raise RuntimeError("forbidden")
        deleted.append((name, ns, force))

    ws = make_ws(qtbot, delete_pod=delete_pod)
    ws.refresh = lambda: None
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: questions.append(a[2]) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    rows = [PodRow(n, "ns", "1/1", "Running", 0, "n", "1d", {}) for n in ("a", "bad", "c")]
    ws.bulk_action("Pod", "force-terminate", rows)
    wait_idle(qtbot, ws)
    assert len(questions) == 1 and "3 pods" in questions[0]
    assert deleted == [("a", "ns", (True,)), ("c", "ns", (True,))]  # one failure does not stop the rest
    assert len(warnings) == 1 and "ns/bad: RuntimeError: forbidden" in warnings[0]


def test_bulk_cancel_does_nothing(qtbot, monkeypatch):
    deleted = []
    ws = make_ws(qtbot, delete_pod=lambda *a: deleted.append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    ws.bulk_action("Pod", "terminate", [PodRow("a", "ns", "1/1", "Running", 0, "n", "1d", {})])
    assert not ws._workers and deleted == []


def node(name, uid="u"):
    return NodeRow(name, "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"metadata": {"uid": uid + name}})


def test_bulk_delete_nodes_asks_yes_no_and_checks_uid(qtbot, monkeypatch):
    deleted, errors = [], []
    ws = make_ws(qtbot, delete_node=lambda *a: deleted.append(a))
    ws.refresh = lambda: None
    ws._on_error = errors.append
    rows = [node("n1"), node("n2")]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    ws.bulk_action("Node", "delete-node", rows)
    assert not ws._workers and deleted == []  # declined
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    ws.bulk_action("Node", "force-delete-node", rows)
    wait_idle(qtbot, ws)
    assert deleted == [("n1", "un1", True), ("n2", "un2", True)]
    nouid = NodeRow("n3", "Ready", "worker", "v1", "1.1.1.1", 4.0, 8.0, "1d", {"metadata": {}})
    ws.bulk_action("Node", "delete-node", [rows[0], nouid])
    assert errors and "n3" in errors[0] and len(deleted) == 2


def test_bulk_cordon_nodes(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, cordon_node=lambda name, flag: calls.append((name, flag)))
    ws.refresh = lambda: None
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    ws.bulk_action("Node", "uncordon", [node("n1"), node("n2")])
    wait_idle(qtbot, ws)
    assert calls == [("n1", False), ("n2", False)]


def test_parse_scale_expressions():
    import pytest

    from klotse.ui.workspace import parse_scale

    assert [parse_scale(t, 3) for t in ("5", "*2", "x2", "×2", "+1", "-1", "/2", " + 2 ")] == [5, 6, 6, 6, 4, 2, 2, 5]
    assert parse_scale("-1", 0) == 0 and parse_scale("/2", 1) == 1 and parse_scale("*2", 0) == 0  # never below zero
    assert parse_scale("*100000", 5) == 10000  # capped
    for bad in ("", "abc", "2.5", "**2", "/0", "-"):
        with pytest.raises(ValueError):
            parse_scale(bad, 3)


def test_bulk_scale_applies_expression_to_each_row_separately(qtbot, monkeypatch):
    calls, started = [], []
    ws = make_ws(qtbot, scale=lambda *a: calls.append(a), scale_status=lambda *a: {})
    ws.open_scale_monitor = lambda kind, row, old, new: started.append((row.name, old, new))
    ws.refresh = lambda: None
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("*2", True))
    rows = [DeploymentRow(n, "ns", "1/1", 1, 1, "1d", {"spec": {"replicas": r}}) for n, r in (("a", 1), ("b", 3), ("c", 0))]
    ws.bulk_action("Deployment", "scale", rows)
    wait_idle(qtbot, ws)
    assert sorted(calls) == [("Deployment", "a", "ns", 2), ("Deployment", "b", "ns", 6), ("Deployment", "c", "ns", 0)]
    assert sorted(started) == [("a", 1, 2), ("b", 3, 6), ("c", 0, 0)]


def test_bulk_scale_rejects_unreadable_expression(qtbot, monkeypatch):
    calls, warnings = [], []
    ws = make_ws(qtbot, scale=lambda *a: calls.append(a))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("double", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    ws.bulk_action("Deployment", "scale", [DeploymentRow("a", "ns", "1/1", 1, 1, "1d", {"spec": {"replicas": 1}})] * 2)
    assert not ws._workers and calls == [] and "double" in warnings[0]
