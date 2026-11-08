import json
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import Qt

from klotse.k8s import trivy
from klotse.k8s.client import KubeService
from klotse.ui.trivy_dialog import ReportDialog, TrivyDialog
from klotse.ui.workspace import ClusterWorkspace

NAMES = ["Name", "Repository", "Tag", "Scanner", "Age", "Critical", "High", "Medium", "Low", "Unknown"]


def meta(workload, container="app", ns="shop", kind="ReplicaSet", name=None):
    return {"name": name or f"{kind.lower()}-{workload}-{container}", "namespace": ns,
            "labels": {trivy.LABEL_KIND: kind, trivy.LABEL_NAME: workload, trivy.LABEL_CONTAINER: container}}


ROWS = [(["r1", "library/nginx", "1.27", "Trivy", "3d", "0", "2", "5", "7", "0"], meta("web")),
        (["r2", "company/api", "v9", "Trivy", "1d", "4", "10", "0", "1", "2"], meta("api", ns="prod", kind="Deployment")),
        (["r3", "library/redis", "7", "Trivy", "9d", "0", "0", "0", "3", ""], meta("cache", container="redis"))]


def test_table_rows_become_reports_worst_first_with_the_workload_from_the_labels():
    rows = trivy.parse_table(NAMES, ROWS)
    assert [(r["workload"], r["critical"], r["high"]) for r in rows] == [("api", 4, 10), ("web", 0, 2), ("cache", 0, 0)]
    api = rows[0]
    assert (api["namespace"], api["workload_kind"], api["container"], api["repository"], api["tag"], api["unknown"]) == ("prod", "Deployment", "app", "company/api", "v9", 2)
    assert rows[2]["unknown"] == 0  # a blank cell counts 0
    assert trivy.totals(rows) == {"critical": 4, "high": 12, "medium": 5, "low": 11, "unknown": 2, "reports": 3, "serious": 2}
    assert "3 reports; 4 critical, 12 high" in trivy.summary_text(rows, "Vulnerabilities") and trivy.summary_text([], "Config audit") == "No config audit reports."


def test_a_report_without_the_operators_labels_or_columns_still_parses():
    rows = trivy.parse_table(["Name", "Scanner", "Critical"], [(["only-name", "Trivy", "2"], {"name": "only-name", "namespace": "x"})])
    assert rows[0]["workload"] == "" and rows[0]["report"] == "only-name" and rows[0]["critical"] == 2 and rows[0]["high"] == 0


VULNS = {"report": {"updateTimestamp": "2026-11-07T10:00:00Z", "vulnerabilities": [
    {"vulnerabilityID": "CVE-2", "resource": "libssl", "installedVersion": "1", "fixedVersion": "2", "severity": "MEDIUM", "title": "m", "score": 5.0},
    {"vulnerabilityID": "CVE-1", "resource": "zlib", "installedVersion": "1", "fixedVersion": "", "severity": "CRITICAL", "title": "c", "score": 9.8},
    {"vulnerabilityID": "CVE-3", "resource": "curl", "installedVersion": "1", "fixedVersion": "3", "severity": "critical", "title": "c2", "score": 7.0}]}}


def test_vulnerabilities_are_sorted_by_severity_then_score():
    found = trivy.vulnerabilities(VULNS)
    assert [v["id"] for v in found] == ["CVE-1", "CVE-3", "CVE-2"] and found[0]["fixed"] == ""
    headings, rows = trivy.details("vulnerabilities", VULNS)
    assert headings[0] == "ID" and rows[0][:2] == ["CVE-1", "zlib"]
    assert trivy.scanned(VULNS) == "2026-11-07 10:00 UTC" and trivy.scanned({}) == ""


def test_only_failed_checks_are_listed_and_exposed_secrets_never_carry_the_match():
    audit = {"report": {"checks": [{"checkID": "KSV001", "title": "ok", "severity": "LOW", "success": True},
                                   {"checkID": "KSV017", "title": "Privileged", "severity": "HIGH", "success": False, "remediation": "Do not set privileged"},
                                   {"checkID": "KSV003", "title": "Caps", "severity": "LOW", "success": False}]}}
    assert [c["id"] for c in trivy.failed_checks(audit)] == ["KSV017", "KSV003"]
    headings, rows = trivy.details("config", audit)
    assert headings == ["Check", "Severity", "Title", "Remediation"] and rows[0] == ["KSV017", "HIGH", "Privileged", "Do not set privileged"]
    secrets = {"report": {"secrets": [{"ruleID": "aws-access-key-id", "title": "AWS Access Key ID", "severity": "CRITICAL", "category": "AWS",
                                       "target": "/app/.env", "match": "AKIAIOSFODNN7EXAMPLE"}]}}
    found = trivy.exposed_secrets(secrets)
    assert found == [{"rule": "aws-access-key-id", "title": "AWS Access Key ID", "severity": "CRITICAL", "category": "AWS", "target": "/app/.env"}]
    headings, rows = trivy.details("secrets", secrets)
    assert "AKIA" not in json.dumps(rows) and "match" not in json.dumps(found)


# ---- the service ------------------------------------------------------------------------------------------------
class FakeRest:
    def __init__(self, pages=None, status=None):
        self.pages, self.urls, self.status = list(pages or []), [], status

    def request(self, method, url, headers=None, _request_timeout=None):
        self.urls.append(url)
        if self.status:
            raise ApiException(status=self.status, reason="Not Found")
        page = self.pages.pop(0)
        table = {"kind": "Table", "columnDefinitions": [{"name": n} for n in NAMES],
                 "rows": [{"cells": cells, "object": {"metadata": m}} for cells, m in page["rows"]], "metadata": {"continue": page.get("next")}}
        return SimpleNamespace(data=json.dumps(table).encode(), status=200)


def trivy_service(rest):
    service = KubeService()
    cfg = SimpleNamespace(host="https://api.example", auth_settings=lambda: {})
    service._api_client = SimpleNamespace(configuration=cfg, default_headers={}, rest_client=rest)
    return service


def test_the_service_reads_the_table_of_the_report_kind_across_pages():
    rest = FakeRest([{"rows": ROWS[:2], "next": "tok"}, {"rows": ROWS[2:]}])
    rows = trivy_service(rest).trivy_reports("vulnerabilities")
    assert len(rows) == 3 and rows[0]["workload"] == "api"
    assert rest.urls[0].startswith("https://api.example/apis/aquasecurity.github.io/v1alpha1/vulnerabilityreports?") and "continue=tok" in rest.urls[1]


def test_no_operator_means_none_and_other_errors_are_raised():
    assert trivy_service(FakeRest(status=404)).trivy_reports("config") is None
    with pytest.raises(ApiException):
        trivy_service(FakeRest(status=403)).trivy_reports("config")


def test_one_report_is_read_in_full_when_asked():
    service = KubeService()
    seen = []
    service._custom = SimpleNamespace(get_namespaced_custom_object=lambda *a, **k: seen.append(a) or VULNS)
    assert service.trivy_report("vulnerabilities", "shop", "r1") is VULNS
    assert seen == [("aquasecurity.github.io", "v1alpha1", "shop", "vulnerabilityreports", "r1")]


# ---- the windows ------------------------------------------------------------------------------------------------
def test_a_tab_loads_when_first_shown_and_sorts_counts_as_numbers(qtbot):
    dialog = TrivyDialog("ctx")
    qtbot.addWidget(dialog)
    asked = []
    dialog.load_requested.connect(asked.append)
    dialog.load_current()
    assert asked == ["vulnerabilities"]
    dialog.show_rows("vulnerabilities", trivy.parse_table(NAMES, ROWS))
    page = dialog.pages["vulnerabilities"]
    assert page.table.rowCount() == 3 and "3 reports" in page.summary.text() and page.table.item(0, 3).text() == "company/api:v9"  # worst first
    dialog.load_current()
    assert asked == ["vulnerabilities"]  # already loaded: not asked again
    dialog.reload_button.click()
    assert asked == ["vulnerabilities", "vulnerabilities"]
    dialog.tabs.setCurrentIndex(2)
    assert asked[-1] == "secrets"
    page.table.sortItems(len(page.columns) - 5, Qt.DescendingOrder)  # the Critical column
    assert [page.table.item(r, 1).text() for r in range(3)][0] == "Deployment/api"
    page.filter.setText("redis")
    assert [page.table.isRowHidden(r) for r in range(3)].count(False) == 1


def test_open_report_finds_the_row_even_when_the_table_is_sorted(qtbot):
    dialog = TrivyDialog("ctx")
    qtbot.addWidget(dialog)
    dialog.show_rows("vulnerabilities", trivy.parse_table(NAMES, ROWS))
    page = dialog.pages["vulnerabilities"]
    page.table.sortItems(1, Qt.AscendingOrder)
    opened = []
    dialog.open_requested.connect(lambda key, row: opened.append((key, row["report"], row["namespace"])))
    for r in range(page.table.rowCount()):
        if page.table.item(r, 1).text() == "Deployment/api":
            page.table.cellDoubleClicked.emit(r, 0)
    assert opened == [("vulnerabilities", meta("api", ns="prod", kind="Deployment")["name"], "prod")]


def test_missing_operator_and_errors_are_explained(qtbot):
    dialog = TrivyDialog("ctx")
    qtbot.addWidget(dialog)
    dialog.show_missing("config")
    assert "not found in this cluster" in dialog.pages["config"].summary.text() and dialog.pages["config"].loaded
    dialog.show_error("rbac", "Forbidden")
    assert "Forbidden" in dialog.pages["rbac"].summary.text()


def test_the_report_window_filters_and_copies_the_visible_rows(qtbot):
    headings, rows = trivy.details("vulnerabilities", VULNS)
    dialog = ReportDialog("Vulnerabilities: web", headings, rows, "2026-11-07 10:00 UTC")
    qtbot.addWidget(dialog)
    assert "3 findings" in dialog.summary.text() and "scanned 2026-11-07" in dialog.summary.text()
    dialog.filter.setText("zlib")
    assert dialog.copy() == 1
    from PySide6.QtWidgets import QApplication
    assert "CVE-1\tzlib" in QApplication.clipboard().text()


def test_the_workspace_reads_on_a_worker_and_opens_a_whole_report(qtbot):
    service = SimpleNamespace(trivy_reports=lambda key: trivy.parse_table(NAMES, ROWS) if key == "vulnerabilities" else None,
                              trivy_report=lambda key, ns, name: VULNS)
    ws = ClusterWorkspace(service, "ctx")
    qtbot.addWidget(ws)
    dialog = ws.show_trivy()
    page = dialog.pages["vulnerabilities"]
    qtbot.waitUntil(lambda: page.table.rowCount() == 3, timeout=5000)
    dialog.tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: "not found in this cluster" in dialog.pages["config"].summary.text(), timeout=5000)
    ws._open_trivy_report("vulnerabilities", page.rows[0])
    qtbot.waitUntil(lambda: bool(getattr(ws, "_report_dialogs", [])), timeout=5000)
    assert ws._report_dialogs[0].table.rowCount() == 3
    ws._report_dialogs[0].close()
    ws.shutdown()
