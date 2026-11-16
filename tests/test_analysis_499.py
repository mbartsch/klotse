"""#499: the analysis windows behave alike: a slow answer cannot replace a newer one, the heading follows the scope, a
running search stops on Esc, and the Metrics query window can be closed with a button or Esc."""
import threading
from types import SimpleNamespace as NS

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from klotse.ui.metrics_query_dialog import MetricsQueryDialog
from klotse.ui.search_dialog import SearchDialog
from klotse.ui.workspace import ClusterWorkspace


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(NS(**service), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    ws._ready = True
    return ws


def held_runner(ws, monkeypatch):
    """Keep each worker's callbacks so the test chooses the order in which answers arrive."""
    calls = []

    def run(fn, on_result, *args, on_error=None):
        calls.append((on_result, on_error))
    monkeypatch.setattr(ws, "_run", run)
    return calls


def test_an_older_capacity_answer_arriving_last_does_not_replace_the_newer_one(qtbot, monkeypatch):
    ws = make_ws(qtbot, list_nodes=lambda: [], list_pods=lambda ns: [])
    calls = held_runner(ws, monkeypatch)
    ws.show_capacity()  # the window's first load
    ws._load_capacity()  # the user pressed Refresh again
    shown = []
    dialog = ws._capacity_dialog
    monkeypatch.setattr(dialog, "show_data", lambda *data: shown.append(data))
    (newer_result, _), = calls[1:]
    (older_result, _), = calls[:1]
    newer_result(([], []))
    older_result(([], []))  # the older answer comes last
    assert len(shown) == 1  # only the newer one was shown


def test_refresh_is_disabled_while_an_analysis_loads(qtbot, monkeypatch):
    ws = make_ws(qtbot, list_nodes=lambda: [], list_pods=lambda ns: [])
    calls = held_runner(ws, monkeypatch)
    ws.show_capacity()
    assert not ws._capacity_dialog.reload_button.isEnabled()
    calls[0][0](([], []))
    assert ws._capacity_dialog.reload_button.isEnabled()


def test_the_health_heading_follows_the_namespace_of_each_load(qtbot, monkeypatch):
    ws = make_ws(qtbot, health_data=lambda ns: ({}, []))
    held_runner(ws, monkeypatch)
    monkeypatch.setattr(ws, "namespace", lambda: "shop")
    ws.show_health()
    assert "namespace shop" in ws._health_dialog.header.text()
    monkeypatch.setattr(ws, "namespace", lambda: "data")
    ws._load_health()
    assert "namespace data" in ws._health_dialog.header.text()


def test_escape_in_search_stops_a_running_search(qtbot):
    dialog = SearchDialog.__new__(SearchDialog)  # reject() needs only the running search's cancel flag
    QDialogBase = SearchDialog.__mro__[1]
    cancel = threading.Event()
    dialog._cancel = cancel
    closed = []
    import unittest.mock as mock
    with mock.patch.object(QDialogBase, "reject", lambda self: closed.append(True)):
        SearchDialog.reject(dialog)
    assert cancel.is_set() and closed == [True]


def test_the_metrics_query_window_has_a_close_button_and_closes_on_escape(qtbot):
    dialog = MetricsQueryDialog("Metrics query", "none", [], lambda *a, **k: None)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert any(b.text() == "Close" for b in dialog.findChildren(QPushButton))
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert not dialog.isVisible()
