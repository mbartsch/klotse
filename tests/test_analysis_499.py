"""#499: the analysis windows behave alike: a slow answer cannot replace a newer one, the heading follows the scope, a
running search stops on Esc, and the Metrics query window can be closed with a button or Esc."""
import threading
from types import SimpleNamespace as NS

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from klotse.k8s import health
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


def test_escape_in_search_stops_a_running_search_and_drops_its_late_answer(qtbot):
    from klotse.k8s.search import Hit
    dialog = SearchDialog("ctx")
    qtbot.addWidget(dialog)
    dialog.show()
    asked = []
    dialog.search_requested.connect(lambda text, ns, cancel: asked.append(cancel))
    dialog.box.setText("web")
    dialog.search_now()
    serial = dialog.serial
    assert dialog.status.text() == "Searching…"
    dialog.reject()  # Esc
    assert asked[0].is_set() and not dialog.isVisible()
    assert dialog.status.text() != "Searching…"  # it no longer claims to search
    dialog.show()  # reopened, then the stopped search's partial answer arrives
    dialog.show_results(serial, [Hit("Pod", "shop", "late")], [])
    assert dialog.table.rowCount() == 0


def test_the_metrics_query_window_has_a_close_button_and_closes_on_escape(qtbot):
    dialog = MetricsQueryDialog("Metrics query", "none", [], lambda *a, **k: None)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert any(b.text() == "Close" for b in dialog.findChildren(QPushButton))
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert not dialog.isVisible()


def test_the_analysis_windows_all_call_the_reload_button_refresh(qtbot):
    from klotse.ui.security_dialog import SecurityDialog
    from klotse.ui.preflight_dialog import PreflightDialog
    for dialog in (SecurityDialog("ctx", "namespace shop"), PreflightDialog("ctx", "namespace shop")):
        qtbot.addWidget(dialog)
        assert dialog.reload_button.text() == "Refresh"


# ---- the rest of the family (#499, second part) -------------------------------------------------------------------------
FINDINGS = [{"severity": severity, "kind": "Deployment", "namespace": "shop", "name": name, "problem": "no probe", "hint": "add one"}
            for severity, name in ((health.INFO, "zeta"), (health.ERROR, "alpha"), (health.WARN, "mid"))]
UNUSED = [{"kind": "ConfigMap", "namespace": "shop", "name": name, "created": "2026-01-01T00:00:00Z", "note": ""}
          for name in ("b-config", "a-config")]


def analysis_service():
    return dict(health_data=lambda ns: ({}, []), pod_security_data=lambda ns: ({}, []), unused_data=lambda ns: ({}, [], []),
                list_pods=lambda ns: [], rbac_data=lambda: {}, preflight_data=lambda: ({}, []), trivy_reports=lambda key: [])


def test_every_analysis_window_disables_refresh_and_drops_an_older_answer(qtbot, monkeypatch):
    ws = make_ws(qtbot, **analysis_service())
    calls = held_runner(ws, monkeypatch)
    for show, attr, shown_by in (("show_security", "_security_dialog", "show_findings"), ("show_unused", "_unused_dialog", "show_result"),
                                 ("show_rightsizing", "_rightsizing_dialog", "show_pods"), ("show_rbac", "_rbac_dialog", "show_data"),
                                 ("show_preflight", "_preflight_dialog", "show_data"), ("show_trivy", "_trivy_dialog", "show_rows")):
        calls.clear()
        getattr(ws, show)()
        dialog = getattr(ws, attr)
        assert not dialog.reload_button.isEnabled(), show  # not clickable while its answer is on the way
        dialog.reload_button.setEnabled(True)
        if show == "show_trivy":
            ws._load_trivy("vulnerabilities")
        else:
            getattr(ws, "_load_" + attr[1:-len("_dialog")])()  # the user pressed Refresh again
        seen = []
        monkeypatch.setattr(dialog, shown_by, lambda *a, s=seen: s.append(a))
        older, newer = calls[0][0], calls[1][0]
        answers = {"show_unused": ((None, None), [], []), "show_preflight": ({}, []), "show_security": ([], []), "show_rbac": {},
                   "show_rightsizing": [], "show_trivy": []}
        newer(answers[show])
        older(answers[show])  # the older answer comes last and is dropped
        assert len(seen) == 1 and dialog.reload_button.isEnabled(), show


def test_the_heading_follows_the_namespace_in_every_namespaced_window(qtbot, monkeypatch):
    ws = make_ws(qtbot, **analysis_service())
    held_runner(ws, monkeypatch)
    for show, attr in (("show_security", "_security_dialog"), ("show_unused", "_unused_dialog"), ("show_rightsizing", "_rightsizing_dialog")):
        monkeypatch.setattr(ws, "namespace", lambda: "shop")
        getattr(ws, show)()
        dialog = getattr(ws, attr)
        assert "namespace shop" in dialog.header.text()
        monkeypatch.setattr(ws, "namespace", lambda: "data")
        getattr(ws, "_load_" + attr[1:-len("_dialog")])()  # Refresh after the scope changed
        assert "namespace data" in dialog.header.text() and "shop" not in dialog.header.text(), show


def test_my_permissions_and_the_change_log_are_not_modal(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDialog
    from klotse.ui.customizations import LabelSettings
    from klotse.ui.main_window import MainWindow
    from klotse.ui.audit_dialog import AuditDialog
    from klotse.ui.permissions_dialog import PermissionsDialog
    from test_app import FakeService
    modal = []
    monkeypatch.setattr(QDialog, "exec", lambda self: modal.append(type(self).__name__) or QDialog.Accepted)
    ws = make_ws(qtbot, my_permissions=lambda ns: {"resources": [], "urls": []})
    ws.show_permissions()
    qtbot.waitUntil(lambda: getattr(ws, "_permissions_window", None) is not None, timeout=5000)
    assert isinstance(ws._permissions_window, PermissionsDialog) and ws._permissions_window.isVisible()
    assert not ws._permissions_window.isModal()
    win = MainWindow(FakeService, label_settings=LabelSettings(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)))
    qtbot.addWidget(win)
    assert win.show_audit_log() is True
    log_window = win._audit_dialog
    assert isinstance(log_window, AuditDialog) and log_window.isVisible() and not log_window.isModal()
    assert win.show_audit_log() is True and win._audit_dialog is log_window  # asked again: the same window, in front
    assert modal == []
    log_window.close()


def test_multi_cluster_keeps_its_rows_when_opened_again(qtbot, tmp_path):
    from test_multi_cluster import window
    from test_app import open_active
    win = window(qtbot, tmp_path)
    open_active(win)
    assert win.show_multi() is True
    dialog = win._multi_dialog
    dialog.read_button.click()
    qtbot.waitUntil(lambda: dialog.read_button.isEnabled() and dialog.table.model.rowCount() > 0, timeout=5000)
    rows = dialog.table.model.rowCount()
    dialog.close()
    assert win.show_multi() is True
    assert win._multi_dialog is dialog and dialog.isVisible() and dialog.table.model.rowCount() == rows
    dialog.close()


def test_export_diagnostics_reuses_its_window_closes_them_all_and_does_not_block_on_close(qtbot):
    import time
    from PySide6.QtCore import QThread
    ws = make_ws(qtbot)
    first = ws.export_diagnostics("shop")
    assert ws.export_diagnostics("shop") is first  # the same namespace: the same window, brought to the front
    other = ws.export_diagnostics("data")
    assert other is not first and first.isVisible() and other.isVisible()

    class Slow(QThread):
        def __init__(self):
            super().__init__()
            self.cancel = threading.Event()

        def run(self):
            time.sleep(1.0)  # an export that takes a moment to notice the cancel

    first.job = Slow()
    first.job.start()
    began = time.perf_counter()
    first.close()
    assert time.perf_counter() - began < 0.5  # closing the window did not wait for the export (it used to wait up to 5 s)
    assert first.job.cancel.is_set() and first.job.isRunning()
    ws.shutdown()  # closes every export window, not only the last one opened, and waits briefly for a running export
    assert not first.isVisible() and not other.isVisible()
    assert not first.job.isRunning()  # no export thread outlives the closed cluster


def test_enter_sends_one_check_and_does_not_also_go_to_a_search_hit(qtbot):
    from klotse.k8s.search import Hit
    from klotse.ui.permissions_dialog import PermissionsDialog
    perms = PermissionsDialog("ctx", "shop", {"resources": [], "urls": []})
    qtbot.addWidget(perms)
    perms.show()
    qtbot.waitExposed(perms)
    asked = []
    perms.check_requested.connect(lambda *a: asked.append(a))
    perms.resource.setText("pods")
    perms.resource.setFocus()
    QTest.keyClick(perms.resource, Qt.Key_Return)
    assert len(asked) == 1  # it used to send two
    search = SearchDialog("ctx")
    qtbot.addWidget(search)
    search.show()
    qtbot.waitExposed(search)
    searched, gone = [], []
    search.search_requested.connect(lambda *a: searched.append(a))
    search.goto_requested.connect(lambda *a: gone.append(a))
    search._fill([Hit("Pod", "shop", "old-hit")], [])
    search.box.setText("web")
    search.box.setFocus()
    QTest.keyClick(search.box, Qt.Key_Return)
    assert len(searched) == 1 and gone == []  # Enter searches; it does not also go to the old hit
    search.close()


def test_loading_and_errors_use_one_wording(qtbot):
    from klotse.ui import analysis_window as aw
    from klotse.ui.capacity_dialog import CapacityDialog
    from klotse.ui.health_dialog import HealthDialog
    from klotse.ui.rbac_dialog import RbacDialog
    from klotse.ui.trivy_dialog import TrivyDialog
    from klotse.ui.unused_dialog import UnusedDialog
    forbidden = 'ApiException: (403)\nReason: Forbidden\nHTTP response body: {"message": "pods is forbidden"}'
    windows = [CapacityDialog("ctx"), HealthDialog("ctx", "namespace shop"), UnusedDialog("ctx", "namespace shop"), RbacDialog("ctx")]
    for dialog in windows:
        qtbot.addWidget(dialog)
        label = getattr(dialog, "summary", None) or dialog.status
        assert label.text() == aw.LOADING
        dialog.show_error("ReadTimeout: read timed out")
        assert label.text().startswith("Could not load: The API server did not answer"), type(dialog).__name__
        dialog.show_error(forbidden)
        assert "Refused" in label.text() and "pods is forbidden" in label.text() and "ApiException" not in label.text()
    trivy = TrivyDialog("ctx")
    qtbot.addWidget(trivy)
    trivy.load_current()
    assert trivy.current_page().summary.text() == aw.LOADING
    trivy.show_error("vulnerabilities", "boom")
    assert trivy.current_page().summary.text() == "Could not load: boom"


def test_nothing_found_and_rows_hidden_by_the_filter_are_said(qtbot):
    from klotse.ui.capacity_dialog import CapacityDialog
    from klotse.ui.health_dialog import HealthDialog
    from klotse.ui.unused_dialog import UnusedDialog
    dialog = HealthDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    dialog.show_findings([])
    assert dialog.summary.text() == "Nothing found." and dialog.found.text() == "Nothing found."
    dialog.show_findings(FINDINGS)
    assert dialog.found.text() == ""
    dialog.filter.setText("alpha")
    assert dialog.found.text() == "2 of 3 hidden by the filter or tick boxes."
    dialog.filter.clear()
    dialog.show_errors.setChecked(False)
    assert dialog.found.text() == "1 of 3 hidden by the filter or tick boxes."
    unused = UnusedDialog("ctx", "namespace shop")
    qtbot.addWidget(unused)
    unused.show_result([], {"ConfigMap": 12, "Secret": 4, "PersistentVolumeClaim": 0})
    assert unused.summary.text().startswith("Nothing found among 12 configmaps")
    capacity = CapacityDialog("ctx")
    qtbot.addWidget(capacity)
    capacity.show_data([], [])
    assert capacity.summary.text().startswith("Nothing found")


def test_window_titles_name_the_cluster_by_its_alias(qtbot, monkeypatch):
    ws = make_ws(qtbot, **analysis_service(), my_permissions=lambda ns: {"resources": [], "urls": []})
    ws.display_name = "Production EU"
    held_runner(ws, monkeypatch)
    expected = {"show_health": "Health check", "show_security": "Pod security", "show_unused": "Unused objects",
                "show_rightsizing": "Right-sizing", "show_rbac": "RBAC lookup", "show_preflight": "Upgrade preflight",
                "show_trivy": "Trivy reports", "show_capacity": "Capacity", "show_search": "Search objects"}
    for show, name in expected.items():
        dialog = getattr(ws, show)()
        assert dialog.windowTitle() == f"{name} — Production EU", show
        if hasattr(dialog, "header"):
            assert "Production EU (ctx)" in dialog.header.text()  # the heading shows both names
    assert ws.export_diagnostics("shop").windowTitle() == "Export diagnostics — Production EU"
    ws._permissions_ready("shop", {"resources": [], "urls": []})
    assert ws._permissions_window.windowTitle() == "My permissions — Production EU"


def test_sorting_by_a_heading_keeps_each_row_tied_to_its_object(qtbot):
    from klotse.k8s.search import Hit
    from klotse.ui.audit_dialog import AuditDialog
    from klotse.ui.health_dialog import HealthDialog
    from klotse.ui.unused_dialog import UnusedDialog
    dialog = HealthDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    dialog.show_findings(FINDINGS)
    assert [dialog.table.item(r, 3).text() for r in range(3)] == ["zeta", "alpha", "mid"]  # the order they came in
    dialog.table.horizontalHeader().sectionClicked.emit(3)  # click Name
    assert [dialog.table.item(r, 3).text() for r in range(3)] == ["alpha", "mid", "zeta"]
    gone = []
    dialog.go_to_requested.connect(lambda *a: gone.append(a))
    dialog._open_row(0, 0)
    assert gone == [("Deployment", "shop", "alpha")]  # the row clicked, not the first finding
    dialog.table.horizontalHeader().sectionClicked.emit(0)  # Severity: by rank, errors first
    assert [dialog.table.item(r, 3).text() for r in range(3)] == ["alpha", "mid", "zeta"]
    dialog.filter.setText("zeta")
    assert [dialog.table.isRowHidden(r) for r in range(3)] == [True, True, False]
    unused = UnusedDialog("ctx", "namespace shop")
    qtbot.addWidget(unused)
    unused.show_result(UNUSED, {"ConfigMap": 2})
    unused.table.horizontalHeader().sectionClicked.emit(2)
    assert unused.table.item(0, 2).text() == "a-config"
    log = NS(path="audit.log", entries=lambda: [dict(time=t, cluster="c", action=a, target="x", result="done", detail="")
                                                 for t, a in (("2", "scale"), ("1", "delete"))])
    audit = AuditDialog(log)
    qtbot.addWidget(audit)
    audit.table.horizontalHeader().sectionClicked.emit(2)
    assert audit.table.item(0, 2).text() == "delete"
    search = SearchDialog("ctx")
    qtbot.addWidget(search)
    search._fill([Hit("Pod", "shop", "zz"), Hit("Pod", "shop", "aa")], [])
    search.table.horizontalHeader().sectionClicked.emit(2)
    went = []
    search.goto_requested.connect(lambda *a: went.append(a))
    search._go(0)
    assert went == [("Pod", "shop", "aa")]


def test_copy_puts_the_rows_shown_on_the_clipboard(qtbot):
    from PySide6.QtWidgets import QApplication
    from klotse.ui.health_dialog import HealthDialog
    dialog = HealthDialog("ctx", "namespace shop")
    qtbot.addWidget(dialog)
    dialog.show_findings(FINDINGS)
    dialog.filter.setText("alpha")
    dialog.copy_button.click()
    lines = QApplication.clipboard().text().splitlines()
    assert lines[0].split("\t") == ["Severity", "Kind", "Namespace", "Name", "Problem", "Hint"] and len(lines) == 2
    assert "alpha" in lines[1]


def test_the_trivy_heading_escapes_the_cluster_name(qtbot):
    from klotse.ui.trivy_dialog import TrivyDialog
    dialog = TrivyDialog("<b>evil</b> & co")
    qtbot.addWidget(dialog)
    assert "&lt;b&gt;evil&lt;/b&gt; &amp; co" in dialog.header.text()


def test_button_rows_use_the_shared_footer(qtbot):
    from PySide6.QtWidgets import QDialogButtonBox
    from klotse.ui.cloud_clusters_dialog import CloudClustersDialog
    from klotse.ui.multi_cluster import MultiClusterDialog
    from klotse.ui.prometheus_source_dialog import PrometheusSourceDialog
    source = NS(kinds=lambda: [("pods", "Pods")], contexts=lambda: ["eu"], table_kind=lambda key: "Pod",
                columns=lambda key: [("Name", "name")], fetch=lambda c, k: (lambda ns: []))
    multi = MultiClusterDialog(source)
    qtbot.addWidget(multi)
    box = multi.findChild(QDialogButtonBox)
    assert box is not None and [b.text() for b in box.buttons()] == ["Close"]
    cloud = CloudClustersDialog(lambda: ("/nowhere/klotse-cloud.klotse", False), [])
    qtbot.addWidget(cloud)
    box = cloud.findChild(QDialogButtonBox)
    assert box is not None and {b.text() for b in box.buttons()} == {"List clusters", "Add selected clusters", "Close"}
    assert box.buttonRole(cloud.add_button) == QDialogButtonBox.AcceptRole
    picker = PrometheusSourceDialog()
    qtbot.addWidget(picker)
    # Test checks the form and sits beside its answer; the footer is OK and Cancel only (it read OK, Test, Cancel)
    assert picker.test_button not in picker.buttons.buttons() and len(picker.buttons.buttons()) == 2


def test_a_check_answer_for_a_replaced_permissions_window_is_dropped(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    ws = make_ws(qtbot, can_i=lambda *a: (True, ""))
    calls = held_runner(ws, monkeypatch)
    perms = {"resources": [], "urls": []}
    old = ws._permissions_ready("shop", perms)
    old.resource.setText("pods")
    old.check_button.click()
    answer, failure = calls[-1]
    ws._permissions_ready("data", perms)  # opened again: the old window is closed and deleted
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    answer((True, "allowed"))  # must not touch the deleted window (RuntimeError before)
    failure("timeout")
    assert ws._permissions_window is not old


def test_refresh_stays_disabled_while_any_trivy_tab_is_loading(qtbot, monkeypatch):
    ws = make_ws(qtbot, trivy_reports=lambda key: [])
    calls = held_runner(ws, monkeypatch)
    dialog = ws.show_trivy()  # the first tab loads
    dialog.tabs.setCurrentIndex(1)  # a second tab starts loading too
    assert len(calls) == 2 and not dialog.reload_button.isEnabled()
    calls[0][0]([])  # the first tab's answer
    assert not dialog.reload_button.isEnabled()  # the second is still on the way
    calls[1][0]([])
    assert dialog.reload_button.isEnabled()


def test_a_refused_check_does_not_point_at_my_permissions_itself(qtbot):
    from klotse.ui.permissions_dialog import PermissionsDialog
    dialog = PermissionsDialog("ctx", "shop", {"resources": [], "urls": []})
    qtbot.addWidget(dialog)
    dialog.show_check(error='ApiException: (403)\nReason: Forbidden\nHTTP response body: {"message": "not allowed"}')
    text = dialog.result.text()
    assert text.startswith("Could not check:") and "not allowed" in text and "My permissions" not in text
