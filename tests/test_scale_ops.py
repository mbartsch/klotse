"""Scaling one workload accepts a number or an operation on its current count (*2 /2 +N -N), like the bulk dialog."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from klotse.ui.workspace import ClusterWorkspace


def row(replicas=4):
    return SimpleNamespace(name="api", namespace="shop", raw={"spec": {"replicas": replicas}})


@pytest.mark.parametrize("text, current, expected", [("7", 4, 7), ("*2", 4, 8), ("x3", 2, 6), ("/2", 5, 3), ("+2", 4, 6),
                                                     ("-2", 4, 2), ("-9", 4, 0), ("0", 4, 0)])
def test_single_scale_takes_a_number_or_an_operation(qtbot, monkeypatch, text, current, expected):
    calls = []
    ws = ClusterWorkspace(SimpleNamespace(scale=lambda *a: calls.append(a)), "ctx")
    qtbot.addWidget(ws)
    ws._scaled = lambda *a: None
    shown = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: shown.append(a[2]) or (text, True))
    ws.scale_resource("Deployment", row(current))
    qtbot.waitUntil(lambda: bool(calls), timeout=3000)
    assert calls == [("Deployment", "api", "shop", expected)]
    assert "*2" in shown[0] and "/2" in shown[0] and "+2" in shown[0] and "-2" in shown[0]  # the dialog lists the operations


def test_a_bad_entry_is_explained_and_nothing_is_scaled(qtbot, monkeypatch):
    calls, warned = [], []
    ws = ClusterWorkspace(SimpleNamespace(scale=lambda *a: calls.append(a)), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("lots", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    ws.scale_resource("Deployment", row())
    assert calls == [] and "Cannot read" in warned[0]


def test_cancel_scales_nothing(qtbot, monkeypatch):
    calls = []
    ws = ClusterWorkspace(SimpleNamespace(scale=lambda *a: calls.append(a)), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("9", False))
    ws.scale_resource("Deployment", row())
    assert calls == []
