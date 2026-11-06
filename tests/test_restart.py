"""Rollout restart of Deployments, StatefulSets and DaemonSets: one row and several, always confirmed first."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMenu, QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui.workspace import ClusterWorkspace


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    ws.refresh = lambda: None
    return ws


def row(name="api"):
    return SimpleNamespace(name=name, namespace="shop", raw={})


def answer(monkeypatch, button):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[2]) or button)
    return asked


@pytest.mark.parametrize("kind, method", [("Deployment", "patch_namespaced_deployment"),
                                          ("StatefulSet", "patch_namespaced_stateful_set"),
                                          ("DaemonSet", "patch_namespaced_daemon_set")])
def test_the_service_stamps_the_pod_template(kind, method):
    calls = []
    svc = object.__new__(KubeService)
    svc._apps = SimpleNamespace(**{method: lambda *a: calls.append(a)})
    svc.restart(kind, "api", "shop")
    [(name, ns, body)] = calls
    stamp = body["spec"]["template"]["metadata"]["annotations"]["kubectl.kubernetes.io/restartedAt"]
    assert (name, ns) == ("api", "shop") and stamp.endswith("Z") and len(stamp) == 20


def test_the_service_refuses_other_kinds():
    svc = object.__new__(KubeService)
    svc._apps = SimpleNamespace()
    with pytest.raises(ValueError):
        svc.restart("ReplicaSet", "api", "shop")


def test_a_restart_asks_first_and_does_nothing_on_no(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, restart=lambda *a: calls.append(a))
    asked = answer(monkeypatch, QMessageBox.No)
    ws.restart_resource("Deployment", row())
    assert calls == [] and "shop/api" in asked[0] and "one by one" in asked[0]


def test_a_confirmed_restart_calls_the_service(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, restart=lambda *a: calls.append(a))
    answer(monkeypatch, QMessageBox.Yes)
    ws.restart_resource("StatefulSet", row())
    qtbot.waitUntil(lambda: not ws._workers, timeout=5000)
    assert calls == [("StatefulSet", "api", "shop")]


def test_bulk_restart_confirms_once_and_restarts_every_row(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, restart=lambda *a: calls.append(a))
    asked = answer(monkeypatch, QMessageBox.Yes)
    ws.bulk_action("Deployment", "restart", [row("a"), row("b")])
    qtbot.waitUntil(lambda: not ws._workers, timeout=5000)
    assert len(asked) == 1 and sorted(calls) == [("Deployment", "a", "shop"), ("Deployment", "b", "shop")]


def test_bulk_restart_declined_restarts_nothing(qtbot, monkeypatch):
    calls = []
    ws = make_ws(qtbot, restart=lambda *a: calls.append(a))
    answer(monkeypatch, QMessageBox.No)
    ws.bulk_action("Deployment", "restart", [row("a"), row("b")])
    assert calls == []


def test_the_menus_offer_restart_only_where_a_rollout_exists(qtbot):
    ws = make_ws(qtbot)
    for table, kind, offered in ((ws.deploy_table, "Deployment", True), (ws.sts_table, "StatefulSet", True),
                                 (ws.rs_table, "ReplicaSet", False), (ws.pod_table, "Pod", False)):
        menu = QMenu()
        table._bulk_menu(menu, [object(), object()])
        assert (f"Restart 2 {kind}s…" in [a.text() for a in menu.actions()]) is offered
