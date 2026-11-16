"""#496: a save the server refuses keeps the typed changes: the editor can be opened again with them."""
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QMessageBox

from klotse.ui.workspace import ClusterWorkspace


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(NS(**service), "ctx")
    qtbot.addWidget(ws)
    ws.show()
    return ws


def refusing_run(ws, monkeypatch):
    """Run the worker's function at once and report its error, as a refused save would come back."""
    def run(fn, on_result, *args, on_error=None):
        try:
            on_result(fn(*args))
        except Exception as e:  # noqa: BLE001 - the test's stand-in for the worker
            on_error(f"{type(e).__name__}: {e}")
    monkeypatch.setattr(ws, "_run", run)


def test_a_refused_save_offers_to_open_the_editor_again_with_the_changes(qtbot, monkeypatch):
    def refuse(kind, body):
        raise RuntimeError("the object has been modified")
    ws = make_ws(qtbot, replace=refuse)
    refusing_run(ws, monkeypatch)
    errors, questions, reopened = [], [], []
    monkeypatch.setattr(ws, "_on_error", errors.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: questions.append(a[2]) or QMessageBox.Yes)
    body = {"kind": "ConfigMap", "metadata": {"name": "web"}, "data": {"mode": "slow"}}
    ws._submit("ConfigMap", "web", body, False, on_refused=lambda b: reopened.append(b))
    assert errors and "modified" in errors[0]
    assert questions and "Open the editor again" in questions[0]
    assert reopened == [body]  # the typed changes come back to the editor, not lost


def test_a_refused_save_with_no_keeps_the_error_only(qtbot, monkeypatch):
    def refuse(kind, body):
        raise RuntimeError("forbidden")
    ws = make_ws(qtbot, replace=refuse)
    refusing_run(ws, monkeypatch)
    monkeypatch.setattr(ws, "_on_error", lambda m: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    reopened = []
    ws._submit("ConfigMap", "web", {"metadata": {"name": "web"}}, False, on_refused=lambda b: reopened.append(b))
    assert reopened == []


def test_a_successful_save_does_not_ask_anything(qtbot, monkeypatch):
    ws = make_ws(qtbot, replace=lambda kind, body: None)
    refusing_run(ws, monkeypatch)
    monkeypatch.setattr(ws, "_saved", lambda kind, name, verb="Saved": None)
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: questions.append(a) or QMessageBox.No)
    ws._submit("ConfigMap", "web", {"metadata": {"name": "web"}}, False, on_refused=lambda b: None)
    assert questions == []
