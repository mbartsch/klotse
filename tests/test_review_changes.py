import copy
import json
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QSettings

from klotse.k8s import client as client_module
from klotse.k8s.client import KubeService, install_dry_run
from klotse.ui.customizations import LabelSettings
from klotse.ui.edit_dialog import EditDialog
from klotse.ui.main_window import MainWindow
from klotse.ui.review_dialog import CANCEL, EDIT, SAVE, ReviewDialog, diff_text
from klotse.ui.workspace import ClusterWorkspace
from test_app import FakeService


# ---- the transport guard: a dry run is only ever sent with dryRun=All ---------------------------------------------
class NewStyleClient:
    def __init__(self):
        self.sent = []

    def call_api(self, method, url, header_params=None, body=None, post_params=None, _request_timeout=None):
        self.sent.append((method, url))
        return "response"


class OldStyleClient:
    def __init__(self):
        self.sent = []

    def call_api(self, resource_path, method, path_params=None, query_params=None, header_params=None, body=None):
        self.sent.append((method, resource_path, list(query_params or [])))
        return "response"


def test_writes_get_dryrun_all_only_while_the_thread_asks_for_it():
    api = NewStyleClient()
    install_dry_run(api)
    install_dry_run(api)  # twice does not wrap twice
    api.call_api("PUT", "https://x/api/v1/namespaces/a/configmaps/c")
    client_module._dry.on = True
    try:
        api.call_api("PUT", "https://x/api/v1/namespaces/a/configmaps/c")
        api.call_api("POST", "https://x/api/v1/pods?fieldManager=klotse")
        api.call_api("GET", "https://x/api/v1/pods")
    finally:
        client_module._dry.on = False
    assert api.sent == [("PUT", "https://x/api/v1/namespaces/a/configmaps/c"), ("PUT", "https://x/api/v1/namespaces/a/configmaps/c?dryRun=All"),
                        ("POST", "https://x/api/v1/pods?fieldManager=klotse&dryRun=All"), ("GET", "https://x/api/v1/pods")]


def test_the_older_client_signature_gets_the_query_parameter():
    api = OldStyleClient()
    install_dry_run(api)
    client_module._dry.on = True
    try:
        api.call_api("/api/v1/pods", "POST", {}, [("fieldManager", "k")])
        api.call_api("/api/v1/pods", "GET", {}, [])
    finally:
        client_module._dry.on = False
    assert api.sent == [("POST", "/api/v1/pods", [("fieldManager", "k"), ("dryRun", "All")]), ("GET", "/api/v1/pods", [])]


def test_a_write_that_cannot_get_the_parameter_is_not_sent():
    class Odd:
        def __init__(self):
            self.sent = []

        def call_api(self, method, url=None, **kw):
            self.sent.append(method)

    api = Odd()
    install_dry_run(api)
    client_module._dry.on = True
    try:
        api.call_api("PUT")  # no url to add the parameter to
    except (TypeError, RuntimeError):
        pass
    finally:
        client_module._dry.on = False
    assert api.sent == []


# ---- the service ---------------------------------------------------------------------------------------------------
def test_dry_run_save_returns_the_stored_object_or_the_servers_reason():
    service = KubeService()
    service._api_client = NewStyleClient()
    seen = []

    def replace(self, kind, body):
        seen.append(client_module._dry.on)
        return SimpleNamespace(raw={**body, "defaulted": True})

    def refuse(self, kind, body):
        error = ApiException(status=422, reason="Unprocessable")
        error.body = json.dumps({"message": "spec.replicas: Invalid value: -3"})
        raise error

    original = KubeService.replace
    try:
        KubeService.replace = SimpleNamespace(__wrapped__=replace)
        assert service.dry_run_save("Deployment", {"a": 1}) == {"ok": True, "stored": {"a": 1, "defaulted": True}}
        assert seen == [True] and client_module._dry.on is False
        KubeService.replace = SimpleNamespace(__wrapped__=refuse)
        result = service.dry_run_save("Deployment", {"a": 1})
        assert result["ok"] is False and "Invalid value: -3" in result["message"]
    finally:
        KubeService.replace = original


def test_it_is_not_a_change_so_read_only_and_the_change_log_do_not_apply():
    service = KubeService()
    service.read_only = True
    log = []
    service.audit = lambda *a, **k: log.append(a)
    service._api_client = NewStyleClient()
    original = KubeService.create
    KubeService.create = SimpleNamespace(__wrapped__=lambda self, kind, body: SimpleNamespace(raw=body))
    try:
        assert service.dry_run_save("ConfigMap", {"x": 1}, creating=True)["ok"] is True
    finally:
        KubeService.create = original
    assert log == []


# ---- the diff and the window ---------------------------------------------------------------------------------------
LIVE = {"metadata": {"name": "c", "namespace": "a", "resourceVersion": "5", "uid": "u", "managedFields": [{"x": 1}]}, "data": {"a": "1"}}


def test_the_diff_shows_only_what_changes_without_the_fields_the_cluster_fills_in():
    after = copy.deepcopy(LIVE)
    after["metadata"]["resourceVersion"] = "6"
    after["data"] = {"a": "2", "b": "new"}
    lines, summary = diff_text("ConfigMap", "c", LIVE, after, False)
    changed = [l for l in lines if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert changed == ["-  a: '1'", "+  a: '2'", "+  b: new"]
    assert "resourceVersion" not in "\n".join(changed) and "managedFields" not in "\n".join(lines) and "added" in summary
    same, text = diff_text("ConfigMap", "c", LIVE, copy.deepcopy(LIVE), False)
    assert same == [] and text.startswith("No change")
    created, text = diff_text("ConfigMap", "c", {}, after, True)
    assert all(l.startswith("+") for l in created) and text == "Would create ConfigMap c"


def test_the_dialog_enables_save_only_when_the_server_accepts_and_defaults_to_cancel(qtbot):
    dialog = ReviewDialog("Review ConfigMap c")
    qtbot.addWidget(dialog)
    assert dialog.verdict == CANCEL and not dialog.save_button.isEnabled()
    dialog.show_result({"ok": False, "message": "spec.replicas: Invalid value: -3"}, [], "")
    assert "refuses" in dialog.status.text() and "-3" in dialog.status.text() and not dialog.save_button.isEnabled()
    dialog.show_result({"ok": True}, ["-a", "+b"], "1 line(s) added, 1 removed")
    assert "accepts" in dialog.status.text() and dialog.save_button.isEnabled() and "<span" in dialog.view.toHtml()
    dialog.save_button.click()
    assert dialog.verdict == SAVE
    other = ReviewDialog("x")
    qtbot.addWidget(other)
    other.back_button.click()
    assert other.verdict == EDIT
    closed = ReviewDialog("x")
    qtbot.addWidget(closed)
    closed.reject()
    assert closed.verdict == CANCEL


# ---- the edit flow ---------------------------------------------------------------------------------------------------
class Dry:
    def __init__(self, result):
        self.result, self.asked, self.saved = result, [], []

    def dry_run_save(self, kind, body, creating=False, custom=None):
        self.asked.append((kind, creating))
        return self.result

    def replace(self, kind, body):
        self.saved.append(("replace", kind))


def flow(qtbot, monkeypatch, answer, result=None, review=True):
    service = Dry(result or {"ok": True, "stored": {"metadata": {"name": "c"}, "data": {"a": "2"}}})
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    ws.review_changes = review
    submitted = []
    ws._submit = lambda *a, **k: submitted.append(a[:3])
    ws._writable = lambda what: True
    dialogs = []

    def exec_(self):
        dialogs.append(self)
        qtbot.waitUntil(lambda: self.save_button.isEnabled() or "refuses" in self.status.text(), timeout=5000)
        getattr(self, answer + "_button", self.save_button).click() if answer != "cancel" else None
        return 0

    monkeypatch.setattr(ReviewDialog, "exec", exec_)
    return ws, service, submitted, dialogs


def yaml_dialog(monkeypatch, bodies):
    """Stand in for the YAML editor: each exec() accepts with the next body."""
    def exec_(self):
        self.result_body = bodies.pop(0)
        return EditDialog.Accepted

    monkeypatch.setattr(EditDialog, "exec", exec_)
    monkeypatch.setattr("klotse.ui.workspace.confirm_risky", lambda *a: True)


def test_save_after_a_good_dry_run_submits(qtbot, monkeypatch):
    ws, service, submitted, dialogs = flow(qtbot, monkeypatch, "save")
    ws._has_form = lambda kind, current: False
    yaml_dialog(monkeypatch, [{"metadata": {"name": "c"}, "data": {"a": "2"}}])
    ws._edit_flow("ConfigMap", {"metadata": {"name": "c"}, "data": {"a": "1"}}, "c", "a")
    assert service.asked == [("ConfigMap", False)] and len(dialogs) == 1 and submitted == [("ConfigMap", "c", {"metadata": {"name": "c"}, "data": {"a": "2"}})]
    assert "1 line(s) added, 1 removed" in dialogs[0].summary.text()


def test_cancel_stores_nothing_and_back_to_editing_reopens_the_editor(qtbot, monkeypatch):
    ws, service, submitted, _dialogs = flow(qtbot, monkeypatch, "cancel")
    ws._has_form = lambda kind, current: False
    yaml_dialog(monkeypatch, [{"metadata": {"name": "c"}}])
    ws._edit_flow("ConfigMap", {"metadata": {"name": "c"}}, "c", "a")
    assert submitted == []
    ws, service, submitted, _dialogs = flow(qtbot, monkeypatch, "back")
    ws._has_form = lambda kind, current: False
    answers = iter(["back", "save"])
    monkeypatch.setattr(ReviewDialog, "exec", lambda self: (qtbot.waitUntil(lambda: self.save_button.isEnabled(), timeout=5000),
                                                            getattr(self, next(answers) + "_button").click(), 0)[2])
    first, second = {"metadata": {"name": "c"}, "data": {"a": "2"}}, {"metadata": {"name": "c"}, "data": {"a": "3"}}
    yaml_dialog(monkeypatch, [first, second])
    ws._edit_flow("ConfigMap", {"metadata": {"name": "c"}}, "c", "a")
    assert submitted == [("ConfigMap", "c", second)]  # edited again after Back to editing, then saved


def test_a_refused_dry_run_does_not_let_the_user_save(qtbot, monkeypatch):
    ws, service, submitted, dialogs = flow(qtbot, monkeypatch, "cancel", result={"ok": False, "message": "invalid"})
    ws._has_form = lambda kind, current: False
    yaml_dialog(monkeypatch, [{"metadata": {"name": "c"}}])
    ws._edit_flow("ConfigMap", {"metadata": {"name": "c"}}, "c", "a")
    assert submitted == [] and not dialogs[0].save_button.isEnabled()


def test_no_review_for_secrets_when_switched_off_or_without_dry_run_support(qtbot, monkeypatch):
    ws, service, submitted, dialogs = flow(qtbot, monkeypatch, "save", review=False)
    assert ws._review("ConfigMap", "c", {}, {}, False) == SAVE and dialogs == [] and service.asked == []
    ws.review_changes = True
    assert ws._review("Secret", "s", {}, {}, False) == SAVE and service.asked == []  # values are never shown
    ws.service = SimpleNamespace()
    assert ws._review("ConfigMap", "c", {}, {}, False) == SAVE


def test_the_view_menu_remembers_the_choice_for_all_clusters(qtbot, tmp_path):
    store = QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)
    win = MainWindow(FakeService, label_settings=LabelSettings(store))
    qtbot.addWidget(win)
    assert win.review_action.isChecked()  # on by default
    from test_app import open_active
    open_active(win)
    ws = win.clusters.currentWidget()
    assert ws.review_changes is True
    win.review_action.setChecked(False)
    assert ws.review_changes is False and str(store.value("edit/review_changes")).lower() == "false"
    again = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)))
    qtbot.addWidget(again)
    assert not again.review_action.isChecked()
