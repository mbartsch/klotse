"""File > Apply YAML…: read manifests, check them with the server, apply them."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui.apply_dialog import ApplyDialog, outcome_line
from klotse.ui.apply_model import ApplyProblem, label, parse_documents
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService, open_active

CM = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cfg\ndata:\n  a: '1'\n"
DEP = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n  namespace: shop\nspec:\n  replicas: 1\n"
LIST = ("apiVersion: v1\nkind: List\nitems:\n  - {apiVersion: v1, kind: ConfigMap, metadata: {name: a}}\n"
        "  - {apiVersion: v1, kind: ConfigMap, metadata: {name: b}}\n")


# ---- reading the text --------------------------------------------------------------------------------------------
def test_several_documents_are_read_in_order_and_empty_ones_skipped():
    docs = parse_documents("---\n" + CM + "---\n---\n" + DEP)
    assert [d["kind"] for d in docs] == ["ConfigMap", "Deployment"]


def test_a_list_stands_for_its_items():
    assert [d["metadata"]["name"] for d in parse_documents(LIST)] == ["a", "b"]


@pytest.mark.parametrize("text, message", [
    ("", "nothing to apply"), ("---\n---\n", "nothing to apply"),
    ("a: [", "not valid YAML"), ("- 1\n- 2\n", "Document 1: is not a mapping"),
    ("kind: ConfigMap\nmetadata: {name: x}\n", "Document 1: is missing apiVersion"),
    ("apiVersion: v1\nkind: ConfigMap\nmetadata: {}\n", "missing metadata.name"),
    ("apiVersion: v1\nkind: Pod\nmetadata: {generateName: x-}\n", "generateName"),
    (CM + "---\nkind: X\n", "Document 2"),
    ("apiVersion: v1\nkind: List\nitems:\n  - {kind: Pod}\n", "Item 1 of a List")])
def test_problems_say_where_and_why(text, message):
    with pytest.raises(ApplyProblem, match=message):
        parse_documents(text)


def test_too_much_text_or_too_many_documents_is_refused():
    with pytest.raises(ApplyProblem, match="too long"):
        parse_documents("a" * 2_000_001)
    with pytest.raises(ApplyProblem, match="Too many"):
        parse_documents("---\n".join([CM] * 101))


def test_labels_name_kind_namespace_and_name():
    assert label({"kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"}}) == "Deployment shop/web"
    assert label({"kind": "ConfigMap", "metadata": {"name": "cfg"}}, "dev") == "ConfigMap dev/cfg"
    assert label({"kind": "Namespace", "metadata": {"name": "x"}}) == "Namespace x"


# ---- the service -------------------------------------------------------------------------------------------------
class Dyn:
    def __init__(self):
        self.calls = []
        self.resources = SimpleNamespace(get=self._get)

    def _get(self, api_version, kind):
        if kind == "Nope":
            raise LookupError("no such kind")
        return SimpleNamespace(namespaced=kind != "Namespace")

    def server_side_apply(self, resource, body=None, name=None, namespace=None, force_conflicts=None, **kw):
        if name == "bad":
            raise ValueError("invalid field")
        self.calls.append((name, namespace, force_conflicts, kw))


def service():
    svc = object.__new__(KubeService)
    dyn = Dyn()
    svc._dynamic_client = lambda: dyn
    return svc, dyn


def test_documents_are_applied_server_side_without_forcing_and_in_the_default_namespace():
    svc, dyn = service()
    docs = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}},
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "dev"}},
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"}}]
    got = svc.apply_documents(docs, "default")
    assert got == [("ConfigMap", "default", "cfg", True, "applied"), ("Namespace", "", "dev", True, "applied"),
                   ("Deployment", "shop", "web", True, "applied")]
    assert dyn.calls[0] == ("cfg", "default", False, {"field_manager": "klotse"})
    assert dyn.calls[1][1] is None  # cluster-scoped: no namespace


def test_a_dry_run_asks_the_server_to_store_nothing():
    svc, dyn = service()
    [(_, _, _, ok, message)] = svc.apply_documents([{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}}], "d", dry_run=True)
    assert ok and "nothing stored" in message and dyn.calls[0][3]["dry_run"] == "All"


def test_one_failing_document_does_not_stop_the_others():
    svc, _ = service()
    docs = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "bad"}},
            {"apiVersion": "v1", "kind": "Nope", "metadata": {"name": "x"}},
            {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ok"}}]
    got = svc.apply_documents(docs, "d")
    assert [r[3] for r in got] == [False, False, True]
    assert "invalid field" in got[0][4] and "no such kind" in got[1][4]


# ---- the dialog --------------------------------------------------------------------------------------------------
def dialog(qtbot):
    d = ApplyDialog("ctx", "shop")
    qtbot.addWidget(d)
    return d


def test_a_bad_text_is_explained_and_nothing_is_sent(qtbot):
    d = dialog(qtbot)
    sent = []
    d.run_requested.connect(lambda *a: sent.append(a))
    d.editor.setPlainText("a: [")
    d.check_button.click()
    d.apply_button.click()
    assert sent == [] and "not valid YAML" in d.result.toPlainText()


def test_check_sends_a_dry_run_without_asking(qtbot, monkeypatch):
    d = dialog(qtbot)
    sent = []
    d.run_requested.connect(lambda docs, dry: sent.append((len(docs), dry)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: pytest.fail("a dry run needs no question"))
    d.editor.setPlainText(CM + "---\n" + DEP)
    d.check_button.click()
    assert sent == [(2, True)] and not d.apply_button.isEnabled()  # busy until the answer comes


def test_apply_asks_first_naming_the_objects_and_no_sends_nothing(qtbot, monkeypatch):
    d = dialog(qtbot)
    sent, asked = [], []
    d.run_requested.connect(lambda *a: sent.append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a[2]) or QMessageBox.No)
    d.editor.setPlainText(CM + "---\n" + DEP)
    d.apply_button.click()
    assert sent == [] and "Cluster: ctx" in asked[0] and "ConfigMap shop/cfg" in asked[0] and "Deployment shop/web" in asked[0]
    assert d.apply_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    d.apply_button.click()
    assert [(len(docs), dry) for docs, dry in sent] == [(2, False)]


def test_results_say_what_happened_to_each_object(qtbot):
    d = dialog(qtbot)
    applied = []
    d.applied.connect(lambda: applied.append(True))
    d.show_results([("ConfigMap", "shop", "cfg", True, "applied"), ("Deployment", "shop", "web", False, "conflict")], dry_run=False)
    text = d.result.toPlainText()
    assert text.startswith("✖ 1 of 2 failed.") and "✔ ConfigMap shop/cfg: applied" in text and "✖ Deployment shop/web: conflict" in text
    assert applied == [True] and d.apply_button.isEnabled()
    d.show_results([("ConfigMap", "shop", "cfg", True, "valid")], dry_run=True)
    assert "Nothing was stored" in d.result.toPlainText() and applied == [True]  # a dry run never counts as a change
    assert outcome_line(("Namespace", "", "dev", True, "applied")) == "✔ Namespace dev: applied"


def test_an_open_file_fills_the_editor_and_a_missing_one_is_explained(qtbot, tmp_path):
    d = dialog(qtbot)
    path = tmp_path / "x.yaml"
    path.write_text(CM, encoding="utf-8")
    d.load_file(str(path))
    assert d.editor.toPlainText() == CM
    d.load_file(str(tmp_path / "missing.yaml"))
    assert "Cannot open the file" in d.result.toPlainText()
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    d.load_file(str(binary))
    assert "Cannot open the file" in d.result.toPlainText()


# ---- the workspace and the menu ----------------------------------------------------------------------------------
def test_the_workspace_runs_the_documents_in_the_background_and_refreshes_after_an_apply(qtbot, monkeypatch):
    calls = []
    answer = [("ConfigMap", "default", "cfg", True, "applied")]
    ws = ClusterWorkspace(SimpleNamespace(apply_documents=lambda *a: calls.append(a) or answer), "ctx")
    qtbot.addWidget(ws)
    refreshed = []
    ws.refresh = lambda: refreshed.append(True)
    d = ws.apply_dialog()
    qtbot.addWidget(d)
    d.editor.setPlainText(CM)
    d.check_button.click()
    qtbot.waitUntil(lambda: "All 1 valid" in d.result.toPlainText(), timeout=5000)
    assert calls[0][1:] == ("default", True) and refreshed == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    d.apply_button.click()
    qtbot.waitUntil(lambda: bool(refreshed), timeout=5000)
    assert calls[1][1:] == ("default", False)


def test_a_failure_of_the_whole_call_is_shown_in_the_dialog(qtbot):
    def boom(*a):
        raise RuntimeError("cannot discover")
    ws = ClusterWorkspace(SimpleNamespace(apply_documents=boom), "ctx")
    qtbot.addWidget(ws)
    d = ws.apply_dialog()
    qtbot.addWidget(d)
    d.editor.setPlainText(CM)
    d.check_button.click()
    qtbot.waitUntil(lambda: "cannot discover" in d.result.toPlainText(), timeout=5000)
    assert d.check_button.isEnabled()


def test_the_file_menu_entry_needs_a_cluster_in_front(qtbot, tmp_path):
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    menu = win.apply_action.associatedObjects()[0]
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ["Apply YAML…", "Exit"]
    menu.aboutToShow.emit()
    assert not win.apply_action.isEnabled() and win.show_apply_yaml() is False
    open_active(win)
    asked = []
    win.workspace("ctx").show_apply_yaml = lambda: asked.append(True)
    menu.aboutToShow.emit()
    assert win.apply_action.isEnabled() and win.show_apply_yaml() is True and asked == [True]
